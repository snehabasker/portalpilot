"""Browser sessions owned by one worker process.

A task's browser lives on exactly one worker. The workflow learns that worker's private task queue when it opens the
session and routes every browser activity there. If the worker dies, the session dies with it; activities on that
queue time out on schedule-to-start and the workflow opens a fresh session elsewhere and resumes from its last
checkpoint (re-login, then the dedupe check, so nothing is submitted twice).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from ..config import settings


class SessionLost(Exception):
    pass


@dataclass
class _Session:
    ctx: BrowserContext
    page: Page
    task_id: str
    last_used: float


class SessionManager:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])

    async def stop(self) -> None:
        for sid in list(self._sessions):
            await self.close(sid)
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def open(self, task_id: str) -> str:
        s = settings()
        async with self._lock:
            if len(self._sessions) >= s.max_browser_sessions:
                await self._evict_idle(force_one=True)
            ctx = await self._browser.new_context(viewport={"width": s.viewport_w, "height": s.viewport_h},
                                                  device_scale_factor=1, locale="en-GB")
            page = await ctx.new_page()
            sid = uuid.uuid4().hex
            self._sessions[sid] = _Session(ctx, page, task_id, time.monotonic())
            return sid

    def page(self, sid: str) -> Page:
        sess = self._sessions.get(sid)
        if sess is None or sess.page.is_closed():
            raise SessionLost(f"session {sid} is not on this worker")
        sess.last_used = time.monotonic()
        return sess.page

    async def close(self, sid: str) -> None:
        sess = self._sessions.pop(sid, None)
        if sess:
            await sess.ctx.close()

    async def _evict_idle(self, force_one: bool = False, idle_s: float = 1800) -> None:
        now = time.monotonic()
        stale = sorted(self._sessions.items(), key=lambda kv: kv[1].last_used)
        for sid, sess in stale:
            if now - sess.last_used > idle_s or force_one:
                await self.close(sid)
                force_one = False

    async def reaper(self) -> None:
        while True:
            await asyncio.sleep(60)
            async with self._lock:
                await self._evict_idle()
