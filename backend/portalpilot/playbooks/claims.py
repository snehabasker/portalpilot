"""Playbook for the legacy claims portal: submit one claim.

A playbook is deliberately *not* a free-roaming agent. The stages, their order and their success checks are code
(reviewable, testable, auditable); the model's job is perception: finding each control on a page whose markup has
no stable ids. Every stage starts by confirming which page it is on, so a stage can be re-run after a crash, a
retry or a human takeover.

Every route to the "Submit claim" button passes through ``dedupe_check``, which searches the portal for this
task's idempotency key. That is what makes the workflow's zero-double-submit guarantee hold even when the outcome of
a previous click is unknown.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date

from playwright.async_api import Error as PlaywrightError

from ..browser.agent import Agent
from ..grounding import Target

STAGES = ["login", "dedupe_check", "fill_policy", "fill_claimant", "fill_incident", "review", "submit"]


class InvalidData(Exception):
    """The portal rejected the row's data. Not retryable; goes back to the data owner."""


class BadCredentials(Exception):
    pass


class UnexpectedPage(Exception):
    pass


class SubmitOutcomeUnknown(Exception):
    """The submit click happened but no confirmation was seen. The claim may or may not exist on the portal."""


@dataclass
class StageContext:
    portal_url: str
    payload: dict
    idempotency_key: str
    username: str | None = None
    password: str | None = None
    mfa_code: str | None = None
    extra: dict = field(default_factory=dict)


def T(kind: str, label: str, **kw) -> Target:
    """Targets phrased exactly like the click policy's training instructions."""
    phr = {"button": f'Click the "{label}" button.', "link": f'Click the "{label}" link.',
           "input": f'Click the "{label}" input field.', "select": f'Click the "{label}" dropdown.'}[kind]
    role = {"button": "button", "link": "link"}.get(kind)
    return Target(phr, label, kind, role=role, label=label if kind in ("input", "select") else None, **kw)


async def expect_heading(agent: Agent, *options: str, timeout_s: float = 15) -> str:
    deadline = asyncio.get_running_loop().time() + timeout_s
    h = ""
    while True:
        try:
            await agent._recover_unavailable()
            await agent.dismiss_overlays()
            h = await agent.heading()
        except PlaywrightError as e:
            # transient while a navigation is in flight ("execution context was destroyed", a read that timed
            # out on a page still waiting for its response): poll again until the deadline
            msg = str(e).lower()
            if not any(k in msg for k in ("context", "navigat", "timeout")):
                raise
        for o in options:
            if h.lower().startswith(o.lower()):
                return o
        if asyncio.get_running_loop().time() > deadline:
            raise UnexpectedPage(f"expected one of {options}, page heading is {h!r} at {agent.page.url}")
        await asyncio.sleep(0.25)


async def _errors(agent: Agent) -> list[str]:
    loc = agent.page.locator(".error")
    return [t.strip() for t in await loc.all_inner_texts() if t.strip()]


# --------------------------------------------------------------------------------------------- stages
async def login(agent: Agent, ctx: StageContext) -> dict:
    await agent.goto(f"{ctx.portal_url}/claims")
    page = await expect_heading(agent, "Claims", "Provider Sign In", "Verify it's you")
    if page == "Claims":
        return {"outcome": "ok", "note": "session already signed in"}
    if page == "Provider Sign In":
        await agent.fill(T("input", "Username"), ctx.username or "")
        await agent.fill(T("input", "Password"), ctx.password or "", secret=True)
        await agent.click(T("button", "Sign in"))
        page = await expect_heading(agent, "Claims", "Verify it's you", "Provider Sign In")
        if page == "Provider Sign In":
            raise BadCredentials("; ".join(await _errors(agent)) or "sign-in rejected")
    if page == "Verify it's you":
        if not ctx.mfa_code:
            return {"outcome": "needs_input", "kind": "mfa_code",
                    "prompt": "The portal is asking for the 6-digit code sent to the account's phone.",
                    "screenshot": (await agent.observe()).screenshot_sha}
        await agent.fill(T("input", "Verification code"), ctx.mfa_code, secret=True)
        await agent.click(T("button", "Verify"))
        if await expect_heading(agent, "Claims", "Verify it's you") != "Claims":
            return {"outcome": "needs_input", "kind": "mfa_code", "prompt": "The code was not accepted. Enter a new one.",
                    "screenshot": (await agent.observe()).screenshot_sha, "consumed_code": True}
    return {"outcome": "ok"}


async def dedupe_check(agent: Agent, ctx: StageContext) -> dict:
    """Search the portal itself for our idempotency key. The only source of truth for 'was it submitted?'."""
    if await agent.heading() != "Claims":
        await agent.click(T("link", "Claims"))
        await expect_heading(agent, "Claims")
    await agent.fill(T("input", "Search by your reference"), ctx.idempotency_key)
    await agent.click(T("button", "Search"))
    await expect_heading(agent, "Claims")
    rows = agent.page.locator("table.results tbody tr")
    for i in range(await rows.count()):
        cells = [c.strip() for c in await rows.nth(i).locator("td").all_inner_texts()]
        if ctx.idempotency_key in cells:
            return {"outcome": "already_submitted", "confirmation_no": cells[0]}
    return {"outcome": "ok"}


async def fill_policy(agent: Agent, ctx: StageContext) -> dict:
    if not (await agent.heading()).startswith("New claim"):
        await agent.click(T("link", "New claim"))
    await expect_heading(agent, "New claim — Policy")
    await agent.fill(T("input", "Policy number"), ctx.payload["policy_number"])
    await agent.click(T("button", "Look up policy"))
    await expect_heading(agent, "New claim — Policy")
    errs = await _errors(agent)
    if errs:
        raise InvalidData("; ".join(errs))
    await agent.click(T("button", "Continue"))
    await expect_heading(agent, "New claim — Claimant")
    return {"outcome": "ok"}


async def fill_claimant(agent: Agent, ctx: StageContext) -> dict:
    await expect_heading(agent, "New claim — Claimant")
    await agent.fill(T("input", "Full name"), ctx.payload["claimant_name"])
    await agent.fill(T("input", "Phone"), ctx.payload.get("claimant_phone") or "")
    await agent.fill(T("input", "Email"), ctx.payload.get("claimant_email") or "")
    await agent.click(T("button", "Continue"))
    if await expect_heading(agent, "New claim — Incident", "New claim — Claimant") != "New claim — Incident":
        raise InvalidData("; ".join(await _errors(agent)) or "claimant page rejected the data")
    return {"outcome": "ok"}


async def fill_incident(agent: Agent, ctx: StageContext) -> dict:
    await expect_heading(agent, "New claim — Incident")
    p = ctx.payload
    await agent.fill(T("input", "Date of loss"), date.fromisoformat(p["loss_date"]).strftime("%d/%m/%Y"))
    await agent.select(T("select", "Type of loss"), p["loss_type"])
    await agent.fill(T("input", "Claim amount"), f"{float(p['amount']):.2f}")
    await agent.fill(T("input", "Description"), p.get("description") or "")
    await agent.fill(T("input", "Your reference"), ctx.idempotency_key)
    await agent.click(T("button", "Continue"))
    if await expect_heading(agent, "New claim — Review", "New claim — Incident") != "New claim — Review":
        raise InvalidData("; ".join(await _errors(agent)) or "incident page rejected the data")
    return {"outcome": "ok"}


async def review(agent: Agent, ctx: StageContext) -> dict:
    """Read back what the portal will submit and compare it with the source row before anyone presses submit."""
    await expect_heading(agent, "New claim — Review")
    text = await agent.text()
    problems = []
    if ctx.idempotency_key not in text:
        problems.append("reference missing on review page")
    if f"{float(ctx.payload['amount']):,.2f}" not in text:
        problems.append("amount on review page does not match the source row")
    if ctx.payload["policy_number"] not in text:
        problems.append("policy number on review page does not match")
    if problems:
        raise InvalidData("; ".join(problems))
    return {"outcome": "ok", "screenshot": (await agent.observe()).screenshot_sha}


CONFIRMATION = re.compile(r"\bCLM-\d{6}\b")


async def submit(agent: Agent, ctx: StageContext) -> dict:
    await expect_heading(agent, "New claim — Review")
    try:
        await agent.click(T("button", "Submit claim"), settle=False)
        await expect_heading(agent, "Claim submitted", timeout_s=20)
    except Exception as e:
        raise SubmitOutcomeUnknown(f"no confirmation after clicking submit: {type(e).__name__}: {e}") from e
    m = CONFIRMATION.search(await agent.text())
    if not m:
        raise SubmitOutcomeUnknown("confirmation page has no claim number")
    return {"outcome": "submitted", "confirmation_no": m.group(0)}


RUNNERS = {"login": login, "dedupe_check": dedupe_check, "fill_policy": fill_policy, "fill_claimant": fill_claimant,
           "fill_incident": fill_incident, "review": review, "submit": submit}
