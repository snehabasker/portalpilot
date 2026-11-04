"""Grounded actions on a page. Every action is a recorded step: screenshot before, grounding trace, the point and
the element it verified against, screenshot after. That record is what the Replay Studio renders."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from playwright.async_api import Error as PWError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PWTimeout

from .. import db, storage
from ..grounding import GroundingFailed, GroundingRouter, Observation, Target
from ..grounding.verify import dom_hash, inspect

DISMISS = re.compile(r"not now|no thanks|close|dismiss|later|skip", re.I)


class PortalUnavailable(Exception):
    """The portal returned its maintenance / 5xx page repeatedly. Retryable at the activity level."""


class Agent:
    def __init__(self, page: Page, task_id: str, stage: str, run_key: str, router: GroundingRouter,
                 actor: str = "agent", heartbeat=None):
        self.page, self.task_id, self.stage, self.run_key = page, task_id, stage, run_key
        self.router, self.actor = router, actor
        self._heartbeat = heartbeat or (lambda *a: None)
        self._n = 0
        self.cost_usd = 0.0

    # ------------------------------------------------------------------ observation
    async def observe(self) -> Observation:
        # bounded: a page stuck mid-navigation (e.g. a hung form POST) must not stall the step
        png = await self.page.screenshot(type="png", timeout=5000, animations="disabled")
        vp = self.page.viewport_size or {"width": 1280, "height": 800}
        try:
            dh = await asyncio.wait_for(dom_hash(self.page), timeout=3)
        except (asyncio.TimeoutError, PWError):
            dh = "unavailable"  # never matches the grounding cache
        sha = await storage.put_screenshot(png, dh, self.page.url)
        return Observation(png, vp["width"], vp["height"], dh, self.page.url, sha)

    async def heading(self) -> str:
        h = self.page.locator("h1").first
        return (await h.inner_text(timeout=5000)).strip() if await h.count() else ""

    async def text(self) -> str:
        return await self.page.inner_text("body", timeout=5000)

    # ------------------------------------------------------------------ navigation
    async def goto(self, url: str) -> None:
        await self._step("navigate", url, None, lambda _: self.page.goto(url, wait_until="load"), ground=None)
        await self.settle()

    async def settle(self, timeout_ms: int = 15000) -> None:
        """Wait for navigation to finish, then deal with the two things legacy portals throw at you: maintenance
        pages and pop-ups."""
        try:
            await self.page.wait_for_load_state("load", timeout=timeout_ms)
        except PWTimeout:
            raise
        await self._recover_unavailable()
        await self.dismiss_overlays()

    async def _recover_unavailable(self) -> None:
        for attempt in range(4):
            body = (await self.text()).lower()
            if "temporarily unavailable" not in body:
                return
            await asyncio.sleep(0.5 * 2 ** attempt)
            await self._step("reload", "portal unavailable page", None,
                             lambda _: self.page.reload(wait_until="load"), ground=None)
        raise PortalUnavailable("portal kept returning 'temporarily unavailable'")

    async def dismiss_overlays(self) -> None:
        dialog = self.page.locator("[role=dialog]:not([hidden])")
        for _ in range(3):
            if not await dialog.count() or not await dialog.first.is_visible():
                return
            labels = [t.strip() for t in await dialog.first.locator("button").all_inner_texts()]
            label = next((t for t in labels if DISMISS.search(t)), None)
            if label is None:
                raise GroundingFailed(Target(f"dismiss the dialog ({labels})", "?"), [])
            await self.click(Target(f'Click the "{label}" button.', label, "button", role="button"), settle=False)
            await asyncio.sleep(0.2)

    # ------------------------------------------------------------------ grounded actions
    async def click(self, target: Target, settle: bool = True, nav_timeout_ms: int = 15000) -> dict:
        async def act(g):
            await self.page.mouse.click(g.x, g.y)
        res = await self._step("click", target.description, None, act, ground=target)
        if settle:
            await asyncio.sleep(0.15)
            await self.settle(nav_timeout_ms)
        return res

    async def fill(self, target: Target, value: str, secret: bool = False) -> dict:
        async def act(g):
            await self.page.mouse.click(g.x, g.y)
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Backspace")
            await self.page.keyboard.type(value, delay=8)
        return await self._step("type_secret" if secret else "type", target.description,
                                "••••••" if secret else value, act, ground=target)

    async def select(self, target: Target, option_label: str) -> dict:
        async def act(g):
            handle = await self.page.evaluate_handle("([x, y]) => document.elementFromPoint(x, y).closest('select')", [g.x, g.y])
            await handle.as_element().select_option(label=option_label)
        return await self._step("select", target.description, option_label, act, ground=target)

    # ------------------------------------------------------------------ operator actions (takeover mode)
    async def operator(self, kind: str, x: int | None, y: int | None, text: str | None) -> dict:
        async def act(_):
            if kind == "click":
                await self.page.mouse.click(x, y)
                await asyncio.sleep(0.15)
                await self.page.wait_for_load_state("load")
            elif kind == "type":
                await self.page.keyboard.type(text or "", delay=8)
            elif kind == "key":
                await self.page.keyboard.press(text or "Enter")
                await asyncio.sleep(0.15)
                await self.page.wait_for_load_state("load")
            elif kind == "navigate":
                await self.page.goto(text, wait_until="load")
        return await self._step(f"operator_{kind}", text if kind in ("navigate", "key") else None,
                                "(typed by operator)" if kind == "type" else None, act, ground=None,
                                operator_point=(x, y) if kind == "click" else None)

    # ------------------------------------------------------------------ recording
    async def _step(self, action: str, target: str | None, value: str | None, act, ground: Target | None,
                    operator_point: tuple[int, int] | None = None) -> dict:
        idx = self._n
        self._n += 1
        key = f"{self.run_key}/{idx}"
        self._heartbeat(key)  # lets Temporal notice a dead worker within the heartbeat timeout, not the stage timeout
        t0 = time.perf_counter()
        before = await self.observe()
        p = await db.pool()
        step_id = await p.fetchval(
            """INSERT INTO task_steps (task_id, step_key, seq, stage, action, target, value, actor, screenshot_before)
               VALUES ($1, $2, (SELECT coalesce(max(seq), 0) + 1 FROM task_steps WHERE task_id = $1), $3, $4, $5, $6, $7, $8)
               ON CONFLICT (task_id, step_key) DO UPDATE SET status = 'started', error = NULL
               RETURNING id""",
            self.task_id, key, self.stage, action, target, value, self.actor, before.screenshot_sha)
        await db.publish("step.started", {"step_id": step_id, "stage": self.stage, "action": action,
                                          "target": target, "screenshot": before.screenshot_sha}, task_id=self.task_id)
        g = None
        fields: dict[str, Any] = {}
        try:
            if ground is not None:
                g = await self.router.ground(self.page, before, ground)
                self.cost_usd += g.cost_usd
                fields = dict(grounder=g.tier, point_x=g.x, point_y=g.y, bbox=g.verification.bbox, verified=True,
                              cost_usd=g.cost_usd)
            elif operator_point:
                el = await inspect(self.page, *operator_point)
                fields = dict(grounder="operator", point_x=operator_point[0], point_y=operator_point[1],
                              bbox=(el or {}).get("bbox"))
            await act(g)
            status, error = "succeeded", None
        except GroundingFailed as e:
            status, error = "failed", str(e)
            fields["grounder_trace"] = [a.as_dict() for a in e.trace]
            fields["cost_usd"] = sum(a.cost_usd for a in e.trace)
            self.cost_usd += fields["cost_usd"]
            raise
        except Exception as e:
            status, error = "failed", f"{type(e).__name__}: {e}"[:500]
            raise
        finally:
            if g is not None:
                fields["grounder_trace"] = [a.as_dict() for a in g.trace]
            after_sha = None
            try:
                after_sha = (await self.observe()).screenshot_sha if not self.page.is_closed() else None
            except Exception:
                pass
            fields.update(status=status, error=error, screenshot_after=after_sha,
                          latency_ms=round((time.perf_counter() - t0) * 1000))
            cols = list(fields)
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
            await p.execute(f"UPDATE task_steps SET {sets}, finished_at = now() WHERE id = $1", step_id,
                            *[fields[c] for c in cols])
            if fields.get("cost_usd"):
                await p.execute("UPDATE tasks SET cost_usd = cost_usd + $2 WHERE id = $1", self.task_id, fields["cost_usd"])
            await db.publish("step.finished", {"step_id": step_id, "stage": self.stage, "action": action,
                                               "status": status, "grounder": fields.get("grounder"),
                                               "screenshot": after_sha, "error": error}, task_id=self.task_id)
        return {"step_id": step_id, **{k: fields.get(k) for k in ("grounder", "point_x", "point_y")}}
