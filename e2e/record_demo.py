"""Records the README demo: drives the real Studio like an operator would, with on-screen captions, against the
local stack running the hostile portal. Each scene is its own Playwright video; ffmpeg joins them.

    GROUNDER_CHAIN=cache,qwen,dom docker compose up -d     (and vLLM serving the click policy)
    python e2e/record_demo.py                             -> docs/demo/portalpilot-demo.mp4 (+ .gif highlight)
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx
import imageio_ffmpeg
from playwright.async_api import async_playwright

UI, API, PORTAL = "http://localhost:5173", "http://localhost:8000", "http://localhost:8080"
TOKEN = "dev-operator-token"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "demo"
RAW = OUT / "_raw"
CSV = ROOT / "e2e" / "claims_messy.csv"
W, H = 1440, 900

api = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}", "X-Operator": "sneha"}, timeout=30)
portal = httpx.Client(base_url=PORTAL, headers={"X-Admin-Token": "portal-admin"}, timeout=30)

CAPTION_JS = """
(text) => {
  let el = document.getElementById('__cap');
  if (!el) {
    el = document.createElement('div'); el.id = '__cap';
    el.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);max-width:1100px;z-index:99999;' +
      'background:rgba(12,14,18,.92);color:#fff;font:600 21px/1.4 Inter,Segoe UI,sans-serif;padding:14px 22px;' +
      'border-radius:10px;border:1px solid #5b9dff;box-shadow:0 8px 30px rgba(0,0,0,.5);text-align:center;transition:opacity .3s';
    document.body.appendChild(el);
  }
  el.innerHTML = text; el.style.opacity = text ? 1 : 0;
}
"""


async def caption(page, text: str, hold: float = 0):
    await page.evaluate(CAPTION_JS, text)
    if hold:
        await page.wait_for_timeout(int(hold * 1000))


def wait_for(fn, timeout=600, every=2.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise TimeoutError(fn)


def open_interventions(kind):
    return [i for i in api.get("/api/interventions").json() if i["kind"] == kind]


class AutoMfa(threading.Thread):
    """Off camera, answers the MFA prompts the video does not show (one is shown being typed in the Studio)."""
    def __init__(self):
        super().__init__(daemon=True)
        self.on = False
        self.done: set[str] = set()

    def run(self):
        while True:
            if self.on:
                for iv in open_interventions("mfa_code"):
                    if iv["id"] not in self.done:
                        code = portal.get("/admin/mfa-code").json()["code"]
                        api.post(f"/api/interventions/{iv['id']}/resolve", json={"value": code})
                        self.done.add(iv["id"])
            time.sleep(2)


async def scene(pw, name: str, body):
    browser = await pw.chromium.launch()
    ctx = await browser.new_context(viewport={"width": W, "height": H}, color_scheme="dark",
                                    record_video_dir=str(RAW / name), record_video_size={"width": W, "height": H})
    page = await ctx.new_page()
    await page.goto(UI)
    await page.evaluate("t => localStorage.setItem('pp_token', t)", TOKEN)
    await body(page)
    await ctx.close()
    await browser.close()
    return next((RAW / name).glob("*.webm"))


async def main():
    shutil.rmtree(RAW, ignore_errors=True)
    RAW.mkdir(parents=True)
    wait_for(lambda: httpx.get(f"{API}/api/healthz").status_code == 200, 120)
    portal.post("/admin/reset")
    portal.post("/admin/chaos", json={"popup_rate": 0.3, "flaky_rate": 0.1, "mfa": True, "slow_submit_next": 1,
                                     "slow_submit_seconds": 40, "seed": 7})
    api.post("/api/credentials", json={"label": "acme-portal", "username": "demo", "password": "demo-pass"})
    auto = AutoMfa()
    auto.start()
    clips = []

    async with async_playwright() as pw:
        # ---------------------------------------------------------------------------------------- 1. onboarding
        async def s1(page):
            await page.goto(f"{UI}/#/batches")
            await caption(page, "<b>PortalPilot</b>: an AI agent that files work into enterprise portals that have <u>no API</u>", 4)
            await caption(page, "A client sends a messy CSV of insurance claims: odd headers, mixed date formats, bad rows", 3)
            await page.set_input_files("input[type=file]", str(CSV))
            await page.wait_for_selector("text=Column mapping")
            await caption(page, "Onboarding maps every header to the claim schema and shows how it decided", 4.5)
            await page.mouse.wheel(0, 380)
            await caption(page, "Rows are normalized and validated. Invalid rows are kept with field errors and never run", 4.5)
            await caption(page, "Each row gets an idempotency key: the same claim can never be filed twice", 3.5)
            await page.mouse.wheel(0, -380)
            await page.get_by_role("button", name="Run 4 claims").click()
            await page.wait_for_url("**/#/batches/*")
            await caption(page, "Temporal starts one durable workflow per claim, three browsers at a time", 4)
            await caption(page, "The portal is hostile on purpose: pop-ups, 503 pages, MFA, random element ids", 5)
        clips.append(await scene(pw, "1_onboard", s1))

        # ---------------------------------------------------------------------------------------- 2. MFA
        wait_for(lambda: open_interventions("mfa_code"), 180)

        async def s2(page):
            await page.goto(f"{UI}/#/inbox")
            await caption(page, "The portal asks for an SMS code. The workflow pauses (no browser or worker is held) and asks a human", 4.5)
            await page.locator("a.card").first.click()
            await page.wait_for_selector("input[placeholder='6-digit code']")
            await caption(page, "The code is sealed server-side and never enters workflow history", 3)
            code = portal.get("/admin/mfa-code").json()["code"]
            await page.fill("input[placeholder='6-digit code']", code)
            await page.get_by_role("button", name="Send code").click()
            auto.on = True
            await caption(page, "The agent resumes. Every click is located visually by my fine-tuned Qwen2-VL-2B model", 6)
            await caption(page, "and checked against the element under that pixel before it happens", 5)
        clips.append(await scene(pw, "2_mfa", s2))

        # ---------------------------------------------------------------------------------------- 3. approval
        wait_for(lambda: open_interventions("approval"), 600)

        async def s3(page):
            await page.goto(f"{UI}/#/inbox")
            await caption(page, "Claims above the 5,000 threshold need a human approval before submit", 3.5)
            await page.locator("a.card", has_text="approval").first.click()
            await page.wait_for_selector("text=Approve & submit")
            await caption(page, "The approver sees exactly what the portal will submit: the review page the agent read back", 5)
            await page.get_by_role("button", name="Approve & submit").click()
            await caption(page, "Approved. The submit goes through a ledger: one click per claim, enforced in SQL", 5)
        clips.append(await scene(pw, "3_approve", s3))

        # ---------------------------------------------------------------------------------------- off camera: finish
        def batch_done():
            b = api.get("/api/batches").json()[0]
            return b if b["status"].startswith("completed") else None
        b = wait_for(batch_done, 900)
        tasks = api.get(f"/api/batches/{b['id']}").json()["tasks"]
        details = {t["id"]: api.get(f"/api/tasks/{t['id']}").json() for t in tasks if t["status"] == "succeeded"}
        hung = next(tid for tid, d in details.items() if d["ledger"] and d["ledger"]["state"] == "reconciled_found")
        rejected = None
        for tid, d in details.items():
            for i, s in enumerate(d["steps"]):
                if any(a["tier"] == "qwen" and a["found"] and a["verified"] is False for a in s["grounder_trace"]) \
                        and s["status"] == "succeeded":
                    rejected = (tid, i)
                    break
            if rejected:
                break

        # ---------------------------------------------------------------------------------------- 4. hung submit replay
        async def s4(page):
            await page.goto(f"{UI}/#/tasks/{hung}")
            await page.wait_for_selector(".timeline .step")
            await caption(page, "Replay: one submit committed on the portal, then the response hung for 40 seconds", 5)
            await caption(page, "A naive bot retries and files the claim twice. This agent did not know the outcome, so it…", 5)
            steps = page.locator(".timeline .step", has_text="Submit claim")
            await steps.last.click()
            await page.locator(".timeline .step", has_text="Search by your reference").last.scroll_into_view_if_needed()
            await caption(page, "…searched the portal for its own reference first (see Workflow history: SubmitOutcomeUnknown → already_submitted)", 6.5)
            await caption(page, "Found it, recorded the claim number, and never clicked again. Submit ledger: <b>1 click</b>", 5)
        clips.append(await scene(pw, "4_reconcile", s4))

        # ---------------------------------------------------------------------------------------- 5. verifier rejects qwen
        if rejected:
            async def s5(page):
                tid, idx = rejected
                await page.goto(f"{UI}/#/tasks/{tid}")
                await page.wait_for_selector(".timeline .step")
                await page.locator(".timeline .step").nth(idx).click()
                await page.wait_for_timeout(800)
                await caption(page, "Here the 2B model clicked the label cell beside a field (red ✕)", 5)
                await caption(page, "The verifier rejected it before any click, and the next tier's point was verified (box)", 5.5)
                await caption(page, "On this portal the small model alone lands 91% of clicks; the rest escalate, never misclick", 5)
            clips.append(await scene(pw, "5_verifier", s5))

        # ---------------------------------------------------------------------------------------- 6. metrics
        async def s6(page):
            await page.goto(f"{UI}/#/metrics")
            await page.wait_for_selector("text=Submit clicks")
            await caption(page, "4 claims filed, 4 submit clicks, 1 unknown outcome recovered, 0 duplicates", 5)
            await caption(page, "Most actions grounded by my own model or the cache; the paid fallback only when needed", 5)
            await caption(page, "Postgres · Temporal · Playwright · React/TS · Terraform on AWS &nbsp;·&nbsp; github.com/snehabasker/portalpilot", 5)
        clips.append(await scene(pw, "6_metrics", s6))

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    parts = []
    for i, c in enumerate(clips):
        p = RAW / f"part{i}.mp4"
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(c), "-vf", "fps=30,format=yuv420p",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "23", str(p)], check=True)
        parts.append(p)
    lst = RAW / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    mp4 = OUT / "portalpilot-demo.mp4"
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    "-movflags", "+faststart", str(mp4)], check=True)
    # README highlight: the reconcile + verifier scenes, smaller and faster
    hl = [p for p, c in zip(parts, clips) if c.parent.name in ("4_reconcile", "5_verifier")]
    lst2 = RAW / "list2.txt"
    lst2.write_text("".join(f"file '{p.as_posix()}'\n" for p in hl))
    gif = OUT / "portalpilot-highlight.gif"
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst2), "-vf",
                    "fps=6,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer",
                    str(gif)], check=True)
    shutil.rmtree(RAW, ignore_errors=True)
    for f in (mp4, gif):
        print(f, f"{f.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    asyncio.run(main())
