"""Grounding eval on the mock portal: how often does each tier land a *verified* click on every control the playbook
uses? Walks the real claim flow (login -> search -> policy -> claimant -> incident -> review) at several viewport
sizes. At each control every tier is asked independently and judged by the same DOM verifier the agent uses; the
flow then advances with the DOM tier so every tier sees identical pages.

    python e2e/grounding_eval.py --qwen-url http://localhost:8001 [--claude] [--popups]

Writes e2e/results/grounding_eval.json and prints a per-control table.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("LOCAL_MASTER_KEY", "x" * 43 + "=")

import httpx  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from portalpilot.grounding.base import Observation  # noqa: E402
from portalpilot.grounding.dom import DomGrounder  # noqa: E402
from portalpilot.grounding.qwen import QwenGrounder  # noqa: E402
from portalpilot.grounding.verify import dom_hash, verify  # noqa: E402
from portalpilot.playbooks.claims import T  # noqa: E402

FLOW = [
    # (page, target, action, value)
    ("login", T("input", "Username"), "fill", "demo"),
    ("login", T("input", "Password"), "fill", "demo-pass"),
    ("login", T("button", "Sign in"), "click", None),
    ("claims", T("input", "Search by your reference"), "fill", "PP-EVAL"),
    ("claims", T("button", "Search"), "click", None),
    ("claims", T("link", "New claim"), "click", None),
    ("policy", T("input", "Policy number"), "fill", "POL-123456"),
    ("policy", T("button", "Look up policy"), "click", None),
    ("policy", T("button", "Continue"), "click", None),
    ("claimant", T("input", "Full name"), "fill", "Jane Doe"),
    ("claimant", T("input", "Phone"), "fill", "07700900123"),
    ("claimant", T("input", "Email"), "fill", "jane@example.com"),
    ("claimant", T("button", "Continue"), "click", None),
    ("incident", T("input", "Date of loss"), "fill", "14/03/2026"),
    ("incident", T("select", "Type of loss"), "select", "Collision"),
    ("incident", T("input", "Claim amount"), "fill", "1250.50"),
    ("incident", T("input", "Description"), "fill", "eval"),
    ("incident", T("input", "Your reference"), "fill", "PP-EVAL"),
    ("incident", T("button", "Continue"), "click", None),
    ("review", T("button", "Submit claim"), "none", None),   # judged, never pressed
]


async def run_flow(page, portal: str, tiers: dict, viewport: tuple[int, int], popups: bool) -> list[dict]:
    dom = DomGrounder()
    out = []
    await page.context.clear_cookies()
    await page.goto(f"{portal}/login")
    for pg, tgt, action, value in FLOW:
        dialog = page.locator("[role=dialog]:not([hidden])")
        if popups and await dialog.count() and await dialog.first.is_visible():
            # judge the pop-up's dismiss button too: it is a real target in production
            dt = T("button", "Not now")
            out.append(await judge(page, tiers, dt, "popup", viewport))
            await dialog.first.get_by_role("button", name="Not now").click()
        out.append(await judge(page, tiers, tgt, pg, viewport))
        png = await page.screenshot()
        obs = Observation(png, *viewport, await dom_hash(page), page.url)
        c = await dom.locate(obs, tgt, page)
        if action == "click":
            await page.mouse.click(c.x, c.y)
            await page.wait_for_load_state("load")
            await asyncio.sleep(0.2)
        elif action == "fill":
            await page.mouse.click(c.x, c.y)
            await page.keyboard.press("Control+A")
            await page.keyboard.type(value)
        elif action == "select":
            h = await page.evaluate_handle("([x,y]) => document.elementFromPoint(x,y).closest('select')", [c.x, c.y])
            await h.as_element().select_option(label=value)
    return out


async def judge(page, tiers: dict, tgt, pg: str, viewport) -> dict:
    png = await page.screenshot()
    obs = Observation(png, *viewport, await dom_hash(page), page.url)
    row = {"page": pg, "target": tgt.description, "viewport": f"{viewport[0]}x{viewport[1]}", "tiers": {}}
    for name, g in tiers.items():
        t0 = time.perf_counter()
        try:
            c = await g.locate(obs, tgt, page)
            ms = round((time.perf_counter() - t0) * 1000)
            if c is None or not c.found:
                row["tiers"][name] = {"found": False, "verified": False, "ms": ms, "reason": c.raw if c else ""}
                continue
            v = await verify(page, c.x, c.y, tgt)
            row["tiers"][name] = {"found": True, "verified": v.ok, "x": c.x, "y": c.y, "ms": ms,
                                  "cost": c.cost_usd, "reason": v.reason, "raw": c.raw}
        except Exception as e:
            row["tiers"][name] = {"found": False, "verified": False, "ms": 0, "reason": f"{type(e).__name__}: {e}"}
    return row


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--portal", default="http://localhost:8080")
    ap.add_argument("--qwen-url", default="http://localhost:8001")
    ap.add_argument("--claude", action="store_true", help="also evaluate the Claude tier (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--popups", action="store_true")
    ap.add_argument("--viewports", default="1280x800,1024x768,1440x900")
    a = ap.parse_args()

    admin = {"X-Admin-Token": "portal-admin"}
    httpx.post(f"{a.portal}/admin/reset", headers=admin).raise_for_status()
    httpx.post(f"{a.portal}/admin/chaos", headers=admin,
               json={"popup_rate": 0.5 if a.popups else 0.0, "flaky_rate": 0.0, "mfa": False, "seed": 3}).raise_for_status()

    tiers: dict = {"qwen": QwenGrounder(url=a.qwen_url, model="merged-clean", timeout_s=30), "dom": DomGrounder()}
    if a.claude:
        from portalpilot.grounding.claude import ClaudeGrounder
        tiers["claude"] = ClaudeGrounder()

    rows: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for vp in a.viewports.split(","):
            w, h = map(int, vp.split("x"))
            ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            page = await ctx.new_page()
            rows += await run_flow(page, a.portal, tiers, (w, h), a.popups)
            await ctx.close()
        await browser.close()

    out = Path(__file__).parent / "results"
    out.mkdir(exist_ok=True)
    (out / "grounding_eval.json").write_text(json.dumps(rows, indent=1))

    names = list(tiers)
    print(f"\n{'control':<46}" + "".join(f"{n:>12}" for n in names))
    by_target: dict[str, list[dict]] = {}
    for r in rows:
        by_target.setdefault(r["target"], []).append(r)
    for tgt, rs in by_target.items():
        cells = []
        for n in names:
            ok = sum(r["tiers"][n]["verified"] for r in rs)
            cells.append(f"{ok}/{len(rs)}")
        print(f"{tgt[:45]:<46}" + "".join(f"{c:>12}" for c in cells))
    print("-" * (46 + 12 * len(names)))
    for n in names:
        ok = sum(r["tiers"][n]["verified"] for r in rows)
        ms = sorted(r["tiers"][n]["ms"] for r in rows if r["tiers"][n].get("ms"))
        cost = sum(r["tiers"][n].get("cost", 0) or 0 for r in rows)
        p50 = ms[len(ms) // 2] if ms else 0
        print(f"{n:<8} verified {ok}/{len(rows)} = {100 * ok / len(rows):.1f}%   p50 {p50} ms   cost ${cost:.4f}")
    # the router's view: cheap tier first, fall back only when it is rejected
    cheap = sum(r["tiers"]["qwen"]["verified"] for r in rows)
    print(f"\nrouter with qwen first: {cheap}/{len(rows)} actions need no paid call; "
          f"{len(rows) - cheap} would escalate to the next tier")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
