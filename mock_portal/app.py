"""Acme Mutual provider portal: a fictional legacy claims portal with no API, built to be hostile to automation.

* element ids and class names are random on every render, so CSS selectors written today break tomorrow
* random "quick survey" modal that blocks the page
* random 503 maintenance pages
* optional SMS-style MFA step
* "slow submit": the claim is committed, then the response hangs for longer than any sane client timeout. A naive
  bot retries and files the claim twice. This is the scenario the agent's submit ledger exists for.
* no duplicate protection: the portal happily accepts the same claim twice

Chaos is controlled at runtime through /admin/chaos so tests can make it deterministic.
"""
from __future__ import annotations

import asyncio
import html
import os
import random
import re
import secrets
import time
from datetime import date, datetime

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="Acme Mutual provider portal (mock)")

USERS = dict(u.split(":", 1) for u in os.environ.get("PORTAL_USERS", "demo:demo-pass").split(","))
ADMIN_TOKEN = os.environ.get("PORTAL_ADMIN_TOKEN", "portal-admin")
LOSS_TYPES = ["Collision", "Theft", "Water damage", "Fire", "Other"]

STATE: dict = {}


def reset() -> None:
    STATE.clear()
    STATE.update(sessions={}, claims=[], mfa_codes={}, submit_count=0,
                 chaos={"popup_rate": 0.0, "flaky_rate": 0.0, "mfa": False, "slow_submit_next": 0,
                        "slow_submit_seconds": 45, "seed": None})
    STATE["rng"] = random.Random()


reset()


def rid(prefix: str = "f") -> str:
    return f"{prefix}{secrets.token_hex(3)}"


CSS = """
body{font-family:Verdana,Geneva,sans-serif;font-size:13px;margin:0;background:#e9ecf1;color:#222}
.top{background:#1d3f72;color:#fff;padding:10px 18px;display:flex;justify-content:space-between;align-items:center}
.top b{font-size:16px} .top a{color:#cfe0ff;margin-left:14px}
.wrap{max-width:900px;margin:18px auto;background:#fff;border:1px solid #b7c0cc;padding:18px 24px}
h1{font-size:19px;color:#1d3f72;border-bottom:2px solid #1d3f72;padding-bottom:6px;margin-top:0}
table.form td{padding:6px 8px;vertical-align:top} label{font-weight:bold}
input[type=text],input[type=password],select,textarea{border:1px solid #8a96a8;padding:4px 6px;font-size:13px;width:260px}
textarea{height:60px}
.btn{background:linear-gradient(#f7f7f7,#d9dde3);border:1px solid #7d8796;padding:5px 14px;font-size:13px;cursor:pointer;margin-right:6px}
.btn.primary{background:linear-gradient(#3f6fb3,#24508f);color:#fff;border-color:#163a6b}
.error{color:#a40000;background:#fde8e8;border:1px solid #e3a3a3;padding:6px 10px;margin:8px 0}
.note{color:#555;font-size:12px}
table.results{border-collapse:collapse;width:100%;margin-top:12px} table.results th,table.results td{border:1px solid #b7c0cc;padding:5px 8px;text-align:left}
table.results th{background:#dde5f0}
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center}
.overlay[hidden]{display:none} .dlg{background:#fff;border:2px solid #1d3f72;padding:20px 26px;width:380px}
.steps{color:#777;font-size:12px;margin-bottom:10px} .steps b{color:#1d3f72}
"""


def page(title: str, body: str, user: str | None = None, popup: bool = False, step: int | None = None) -> HTMLResponse:
    nav = (f'<span>Signed in as {html.escape(user)} <a href="/claims">Claims</a><a href="/logout">Sign out</a></span>'
           if user else "<span>Provider services</span>")
    steps = ""
    if step:
        names = ["Policy", "Claimant", "Incident", "Review"]
        steps = '<div class="steps">' + " &rsaquo; ".join(
            f"<b>{n}</b>" if i + 1 == step else n for i, n in enumerate(names)) + "</div>"
    modal = ""
    if popup:
        modal = f"""<div class="overlay" role="dialog" aria-modal="true" id="{rid('d')}">
          <div class="dlg"><h2>Quick survey</h2><p>How satisfied are you with the new provider portal?
          It only takes 2 minutes.</p>
          <button class="btn primary" type="button" onclick="this.closest('[role=dialog]').hidden=true">Take survey</button>
          <button class="btn" type="button" onclick="this.closest('[role=dialog]').hidden=true">Not now</button></div></div>"""
    return HTMLResponse(f"""<!doctype html><html><head><title>{html.escape(title)} - Acme Mutual</title>
      <style>{CSS}</style></head><body><div class="top"><b>Acme Mutual &middot; Provider Portal</b>{nav}</div>
      <div class="wrap {rid('w')}">{steps}<h1>{html.escape(title)}</h1>{body}</div>{modal}</body></html>""")


def field(label: str, name: str, value: str = "", kind: str = "text", note: str = "") -> str:
    fid = rid()
    if kind == "textarea":
        ctl = f'<textarea id="{fid}" name="{name}">{html.escape(value)}</textarea>'
    elif kind == "select":
        opts = "".join(f'<option{" selected" if o == value else ""}>{o}</option>' for o in ["-- choose --"] + LOSS_TYPES)
        ctl = f'<select id="{fid}" name="{name}">{opts}</select>'
    else:
        ctl = f'<input id="{fid}" type="{kind}" name="{name}" value="{html.escape(value)}" autocomplete="off">'
    note = f'<div class="note">{note}</div>' if note else ""
    return f'<tr><td><label for="{fid}">{label}</label></td><td>{ctl}{note}</td></tr>'


def errors(errs: list[str]) -> str:
    return "".join(f'<div class="error">{html.escape(e)}</div>' for e in errs)


# --------------------------------------------------------------------------------------------- sessions
def session(request: Request) -> dict | None:
    return STATE["sessions"].get(request.cookies.get("PSESSION", ""))


def signed_in(request: Request) -> dict | None:
    s = session(request)
    return s if s and s.get("user") and s.get("mfa_ok") else None


def popup_now() -> bool:
    return STATE["rng"].random() < STATE["chaos"]["popup_rate"]


@app.middleware("http")
async def flaky(request: Request, call_next):
    if request.method == "GET" and not request.url.path.startswith(("/admin", "/healthz")) \
            and STATE["rng"].random() < STATE["chaos"]["flaky_rate"]:
        return HTMLResponse('<!doctype html><html><body style="font-family:Verdana;padding:40px"><h1>Service Unavailable</h1>'
                            "<p>The service is temporarily unavailable. Please try again in a few moments.</p></body></html>",
                            status_code=503)
    return await call_next(request)


@app.get("/")
async def root():
    return RedirectResponse("/claims", 303)


@app.get("/login")
async def login_form(request: Request, error: str = ""):
    body = f"""{errors([error] if error else [])}<form method="post" action="/login"><table class="form">
      {field("Username", "username")}{field("Password", "password", kind="password")}</table>
      <button class="btn primary {rid('b')}" type="submit">Sign in</button>
      <a href="#" class="note">Forgotten password?</a></form>"""
    return page("Provider Sign In", body)


@app.post("/login")
async def login(username: str = Form(""), password: str = Form("")):
    if USERS.get(username) != password:
        return RedirectResponse("/login?error=Invalid+username+or+password", 303)
    sid = secrets.token_urlsafe(16)
    mfa = STATE["chaos"]["mfa"]
    STATE["sessions"][sid] = {"user": username, "mfa_ok": not mfa, "draft": {}}
    if mfa:  # one SMS code per user, valid for 5 minutes (concurrent sign-ins share it, like a real SMS)
        code, issued = STATE["mfa_codes"].get(username, (None, 0.0))
        if code is None or time.time() - issued > 300:
            STATE["mfa_codes"][username] = (f"{STATE['rng'].randint(0, 999999):06d}", time.time())
    r = RedirectResponse("/mfa" if mfa else "/claims", 303)
    r.set_cookie("PSESSION", sid, httponly=True)
    return r


@app.get("/mfa")
async def mfa_form(request: Request, error: str = ""):
    if not session(request):
        return RedirectResponse("/login", 303)
    body = f"""{errors([error] if error else [])}<p>Enter the 6-digit code sent to your phone ending ••42.</p>
      <form method="post" action="/mfa"><table class="form">{field("Verification code", "code")}</table>
      <button class="btn primary" type="submit">Verify</button></form>"""
    return page("Verify it's you", body)


@app.post("/mfa")
async def mfa(request: Request, code: str = Form("")):
    s = session(request)
    if not s:
        return RedirectResponse("/login", 303)
    expected, issued = STATE["mfa_codes"].get(s["user"], (None, 0.0))
    if expected is None or time.time() - issued > 300 or expected != code.strip():
        return RedirectResponse("/mfa?error=That+code+is+not+valid", 303)
    s["mfa_ok"] = True
    return RedirectResponse("/claims", 303)


@app.get("/logout")
async def logout(request: Request):
    STATE["sessions"].pop(request.cookies.get("PSESSION", ""), None)
    return RedirectResponse("/login", 303)


def gate(request: Request):
    s = session(request)
    if not s or not s.get("user"):
        return None, RedirectResponse("/login", 303)
    if not s.get("mfa_ok"):
        return None, RedirectResponse("/mfa", 303)
    return s, None


# --------------------------------------------------------------------------------------------- claims
@app.get("/claims")
async def claims(request: Request, ref: str = ""):
    s, redirect = gate(request)
    if redirect:
        return redirect
    rows = [c for c in STATE["claims"] if ref.strip() and c["reference"] == ref.strip()] if ref.strip() \
        else STATE["claims"][-10:][::-1]
    if rows:
        trs = "".join(f"<tr><td>{c['claim_no']}</td><td>{html.escape(c['reference'])}</td><td>{c['policy']}</td>"
                      f"<td>{html.escape(c['claimant'])}</td><td>{c['amount']:,.2f}</td><td>Received</td></tr>" for c in rows)
        table = (f'<table class="results"><thead><tr><th>Claim no.</th><th>Your reference</th><th>Policy</th>'
                 f'<th>Claimant</th><th>Amount</th><th>Status</th></tr></thead><tbody>{trs}</tbody></table>')
    else:
        table = '<p class="note">No claims match.</p>'
    body = f"""<p><a class="btn primary" href="/claims/new/policy" role="link">New claim</a></p>
      <form method="get" action="/claims"><table class="form">{field("Search by your reference", "ref", ref)}</table>
      <button class="btn" type="submit">Search</button></form>
      <h2 style="font-size:15px;margin-top:18px">{'Search results' if ref else 'Recent claims'}</h2>{table}"""
    return page("Claims", body, s["user"], popup=popup_now())


def policy_holder(policy: str) -> str | None:
    m = re.fullmatch(r"POL-(\d{6})", policy.strip().upper())
    if not m or m.group(1).startswith("9"):
        return None
    names = ["J. Okafor", "M. Lindqvist", "A. Moreau", "R. Takahashi", "S. Patel", "L. García"]
    return names[int(m.group(1)) % len(names)]


@app.get("/claims/new/policy")
async def new_policy(request: Request, error: str = ""):
    s, redirect = gate(request)
    if redirect:
        return redirect
    d = s["draft"]
    found = ""
    if d.get("holder"):
        found = (f'<p>Policyholder: <b>{html.escape(d["holder"])}</b></p>'
                 '<form method="post" action="/claims/new/policy/continue"><button class="btn primary" type="submit">Continue</button></form>')
    body = f"""{errors([error] if error else [])}<form method="post" action="/claims/new/policy"><table class="form">
      {field("Policy number", "policy", d.get("policy", ""), note="Format POL-123456")}</table>
      <button class="btn" type="submit">Look up policy</button></form>{found}"""
    return page("New claim — Policy", body, s["user"], popup=popup_now(), step=1)


@app.post("/claims/new/policy")
async def lookup_policy(request: Request, policy: str = Form("")):
    s, redirect = gate(request)
    if redirect:
        return redirect
    holder = policy_holder(policy)
    s["draft"] = {"policy": policy.strip().upper()} | ({"holder": holder} if holder else {})
    return RedirectResponse("/claims/new/policy" + ("" if holder else "?error=Policy+not+found"), 303)


@app.post("/claims/new/policy/continue")
async def policy_continue(request: Request):
    s, redirect = gate(request)
    if redirect:
        return redirect
    if not s["draft"].get("holder"):
        return RedirectResponse("/claims/new/policy?error=Look+up+a+policy+first", 303)
    return RedirectResponse("/claims/new/claimant", 303)


@app.get("/claims/new/claimant")
async def new_claimant(request: Request, error: str = ""):
    s, redirect = gate(request)
    if redirect:
        return redirect
    d = s["draft"]
    if not d.get("holder"):
        return RedirectResponse("/claims/new/policy", 303)
    body = f"""{errors(error.split("|") if error else [])}<form method="post" action="/claims/new/claimant"><table class="form">
      {field("Full name", "name", d.get("claimant", ""))}{field("Phone", "phone", d.get("phone", ""))}
      {field("Email", "email", d.get("email", ""))}</table>
      <button class="btn primary" type="submit">Continue</button></form>"""
    return page("New claim — Claimant", body, s["user"], popup=popup_now(), step=2)


@app.post("/claims/new/claimant")
async def post_claimant(request: Request, name: str = Form(""), phone: str = Form(""), email: str = Form("")):
    s, redirect = gate(request)
    if redirect:
        return redirect
    errs = []
    if len(name.strip()) < 2:
        errs.append("Full name is required")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email.strip()):
        errs.append("Email address is not valid")
    s["draft"].update(claimant=name.strip(), phone=phone.strip(), email=email.strip())
    if errs:
        return RedirectResponse("/claims/new/claimant?error=" + "|".join(errs), 303)
    return RedirectResponse("/claims/new/incident", 303)


@app.get("/claims/new/incident")
async def new_incident(request: Request, error: str = ""):
    s, redirect = gate(request)
    if redirect:
        return redirect
    d = s["draft"]
    if not d.get("claimant"):
        return RedirectResponse("/claims/new/claimant", 303)
    body = f"""{errors(error.split("|") if error else [])}<form method="post" action="/claims/new/incident"><table class="form">
      {field("Date of loss", "loss_date", d.get("loss_date", ""), note="DD/MM/YYYY")}
      {field("Type of loss", "loss_type", d.get("loss_type", ""), kind="select")}
      {field("Claim amount", "amount", d.get("amount_raw", ""), note="In GBP, numbers only")}
      {field("Description", "description", d.get("description", ""), kind="textarea")}
      {field("Your reference", "reference", d.get("reference", ""), note="Optional. Shown on all correspondence.")}</table>
      <button class="btn primary" type="submit">Continue</button></form>"""
    return page("New claim — Incident", body, s["user"], popup=popup_now(), step=3)


@app.post("/claims/new/incident")
async def post_incident(request: Request, loss_date: str = Form(""), loss_type: str = Form(""), amount: str = Form(""),
                        description: str = Form(""), reference: str = Form("")):
    s, redirect = gate(request)
    if redirect:
        return redirect
    errs = []
    try:
        d = datetime.strptime(loss_date.strip(), "%d/%m/%Y").date()
        if d > date.today():
            errs.append("Date of loss cannot be in the future")
    except ValueError:
        errs.append("Date of loss must be DD/MM/YYYY")
    if loss_type not in LOSS_TYPES:
        errs.append("Choose a type of loss")
    try:
        amt = float(amount.replace(",", ""))
        if amt <= 0:
            raise ValueError
    except ValueError:
        amt = None
        errs.append("Claim amount must be a positive number")
    s["draft"].update(loss_date=loss_date.strip(), loss_type=loss_type, amount_raw=amount.strip(), amount=amt,
                      description=description.strip(), reference=reference.strip()[:40])
    if errs:
        return RedirectResponse("/claims/new/incident?error=" + "|".join(errs), 303)
    return RedirectResponse("/claims/new/review", 303)


@app.get("/claims/new/review")
async def review(request: Request):
    s, redirect = gate(request)
    if redirect:
        return redirect
    d = s["draft"]
    if d.get("amount") is None:
        return RedirectResponse("/claims/new/incident", 303)
    rows = [("Policy", d["policy"]), ("Policyholder", d["holder"]), ("Claimant", d["claimant"]), ("Phone", d["phone"]),
            ("Email", d["email"]), ("Date of loss", d["loss_date"]), ("Type of loss", d["loss_type"]),
            ("Claim amount", f"{d['amount']:,.2f}"), ("Description", d["description"]), ("Your reference", d["reference"])]
    trs = "".join(f"<tr><th>{k}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
    body = f"""<p>Check the details below. Once submitted a claim cannot be withdrawn online.</p>
      <table class="results">{trs}</table><br><form method="post" action="/claims/new/submit">
      <button class="btn primary" type="submit">Submit claim</button>
      <a href="/claims/new/incident" class="btn">Back</a></form>"""
    return page("New claim — Review", body, s["user"], popup=popup_now(), step=4)


@app.post("/claims/new/submit")
async def submit(request: Request):
    s, redirect = gate(request)
    if redirect:
        return redirect
    d = s["draft"]
    if d.get("amount") is None:
        return RedirectResponse("/claims/new/policy", 303)
    STATE["submit_count"] += 1
    claim_no = f"CLM-{len(STATE['claims']) + 1:06d}"
    STATE["claims"].append({"claim_no": claim_no, "reference": d.get("reference", ""), "policy": d["policy"],
                            "claimant": d["claimant"], "amount": d["amount"], "loss_type": d["loss_type"],
                            "loss_date": d["loss_date"], "submitted_at": datetime.utcnow().isoformat()})
    s["draft"] = {}
    if STATE["chaos"]["slow_submit_next"] > 0:  # committed, but the client will not hear about it in time
        STATE["chaos"]["slow_submit_next"] -= 1
        await asyncio.sleep(STATE["chaos"]["slow_submit_seconds"])
    return RedirectResponse(f"/claims/{claim_no}", 303)


@app.get("/claims/{claim_no}")
async def claim(request: Request, claim_no: str):
    s, redirect = gate(request)
    if redirect:
        return redirect
    c = next((c for c in STATE["claims"] if c["claim_no"] == claim_no), None)
    if not c:
        raise HTTPException(404)
    body = f"""<p>Thank you. Your claim number is <b>{c['claim_no']}</b>.</p>
      <p class="note">Reference: {html.escape(c['reference'])} &middot; Policy {c['policy']} &middot; {c['amount']:,.2f}</p>
      <p><a href="/claims">Back to claims</a></p>"""
    return page("Claim submitted", body, s["user"])


# --------------------------------------------------------------------------------------------- admin (tests only)
def admin(token: str | None) -> None:
    if token != ADMIN_TOKEN:
        raise HTTPException(401)


@app.get("/admin/claims")
async def admin_claims(x_admin_token: str | None = Header(None)):
    admin(x_admin_token)
    return {"claims": STATE["claims"], "submit_count": STATE["submit_count"]}


@app.post("/admin/chaos")
async def admin_chaos(request: Request, x_admin_token: str | None = Header(None)):
    admin(x_admin_token)
    STATE["chaos"].update(await request.json())
    if STATE["chaos"].get("seed") is not None:
        STATE["rng"].seed(STATE["chaos"]["seed"])
    return STATE["chaos"]


@app.get("/admin/mfa-code")
async def admin_mfa(username: str = "demo", x_admin_token: str | None = Header(None)):
    """Stands in for the human reading the SMS."""
    admin(x_admin_token)
    return {"code": STATE["mfa_codes"].get(username, (None, 0))[0]}


@app.post("/admin/reset")
async def admin_reset(x_admin_token: str | None = Header(None)):
    admin(x_admin_token)
    reset()
    return {"ok": True}


@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True})
