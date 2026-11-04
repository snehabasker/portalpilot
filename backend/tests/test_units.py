"""Unit tests: Qwen output parsing and scaling, the tiered router, the vault, and CSV onboarding."""
from __future__ import annotations

import json
import os

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken

os.environ.setdefault("LOCAL_MASTER_KEY", Fernet.generate_key().decode())

from portalpilot import vault  # noqa: E402
from portalpilot.grounding.base import Candidate, Observation, Target, Verification  # noqa: E402
from portalpilot.grounding.base import GroundingFailed  # noqa: E402
from portalpilot.grounding.qwen import QwenGrounder, parse_click  # noqa: E402
from portalpilot.grounding.router import GroundingRouter  # noqa: E402
from portalpilot.onboarding import parse_csv, parse_date  # noqa: E402

OBS = Observation(b"png", 1280, 800, "dom-1", "http://x")
TGT = Target('Click the "Sign in" button.', "Sign in", "button")


# ------------------------------------------------------------------------------------------ qwen
@pytest.mark.parametrize("text,expected", [
    ("(500, 250)", (500.0, 250.0)),
    ("<|box_start|>(12,999)<|box_end|>", (12.0, 999.0)),
    ("(733, 414, 886,", None),            # truncated box must not pass as a point
    ("(1, 2) or (3, 4)", None),           # spraying several points
    ("(1200, 10)", None),                 # out of range
    ("the button", None),
])
def test_parse_click(text, expected):
    assert parse_click(text) == expected


async def test_qwen_scales_to_pixels_and_sends_training_prompt():
    seen = {}

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen["body"] = body
        return httpx.Response(200, json={"choices": [{"message": {"content": "(500, 250)"}}]})

    g = QwenGrounder(url="http://qwen", model="merged-clean")
    g._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c = await g.locate(OBS, TGT)
    assert (c.x, c.y, c.found) == (640, 200, True)
    content = seen["body"]["messages"][0]["content"]
    assert content[0]["type"] == "image_url"                       # image first, as in training
    assert content[1]["text"].startswith('Click the "Sign in" button.\nReply with only the click point')
    assert seen["body"]["temperature"] == 0


# ------------------------------------------------------------------------------------------ router
class FakeTier:
    def __init__(self, name, cand, cost=0.0):
        self.name, self.cand, self.cost, self.calls = name, cand, cost, 0

    async def locate(self, obs, target, page):
        self.calls += 1
        if isinstance(self.cand, Exception):
            raise self.cand
        if self.cand is None:
            return Candidate(0, 0, self.cost, found=False, raw="not found")
        return Candidate(*self.cand, cost_usd=self.cost)


class MemCache:
    def __init__(self):
        self.d, self.hits, self.misses = {}, 0, 0

    async def get(self, dh, t):
        return self.d.get((dh, t.description))

    async def hit(self, dh, t, ok):
        if ok:
            self.hits += 1
        else:
            self.misses += 1

    async def put(self, dh, t, x, y, tier):
        self.d[(dh, t.description)] = (x, y)


def verifier_at(good: tuple[int, int]):
    async def v(page, x, y, target):
        ok = abs(x - good[0]) < 20 and abs(y - good[1]) < 10
        return Verification(ok, "ok" if ok else "hit the wrong element", {"bbox": {"x": 600, "y": 190, "w": 80, "h": 24}})
    return v


async def test_router_prefers_cheap_tier_when_verified():
    qwen, claude = FakeTier("qwen", (640, 200)), FakeTier("claude", (640, 200), 0.01)
    r = GroundingRouter(("qwen", "claude"), {"qwen": qwen, "claude": claude}, cache=None, verifier=verifier_at((640, 200)))
    g = await r.ground(None, OBS, TGT)
    assert g.tier == "qwen" and claude.calls == 0 and g.cost_usd == 0


async def test_router_escalates_when_small_model_misses_and_caches_the_fix():
    qwen, claude = FakeTier("qwen", (100, 700)), FakeTier("claude", (642, 201), 0.012)
    cache = MemCache()
    r = GroundingRouter(("cache", "qwen", "claude"), {"qwen": qwen, "claude": claude}, cache=cache,
                        verifier=verifier_at((640, 200)))
    g = await r.ground(None, OBS, TGT)
    assert g.tier == "claude" and g.cost_usd == pytest.approx(0.012)
    assert [a.tier for a in g.trace] == ["cache", "qwen", "claude"]
    assert g.trace[1].found and g.trace[1].verified is False
    # same page structure again: served from cache, no model calls at all
    g2 = await r.ground(None, OBS, TGT)
    assert g2.tier == "cache" and qwen.calls == 1 and claude.calls == 1 and cache.hits == 1


async def test_router_survives_a_broken_tier():
    qwen, claude = FakeTier("qwen", ConnectionError("vllm down")), FakeTier("claude", (640, 200), 0.01)
    r = GroundingRouter(("qwen", "claude"), {"qwen": qwen, "claude": claude}, cache=None, verifier=verifier_at((640, 200)))
    g = await r.ground(None, OBS, TGT)
    assert g.tier == "claude" and "vllm down" in g.trace[0].reason


async def test_router_fails_loudly_with_full_trace():
    r = GroundingRouter(("qwen", "claude"), {"qwen": FakeTier("qwen", (1, 1)), "claude": FakeTier("claude", None, 0.01)},
                        cache=None, verifier=verifier_at((640, 200)))
    with pytest.raises(GroundingFailed) as e:
        await r.ground(None, OBS, TGT)
    assert [a.tier for a in e.value.trace] == ["qwen", "claude"]
    assert sum(a.cost_usd for a in e.value.trace) == pytest.approx(0.01)   # cost of a failure is still recorded


# ------------------------------------------------------------------------------------------ vault
def test_vault_roundtrip_and_tamper_detection():
    kp = vault.LocalKeyProvider(Fernet.generate_key().decode())
    s = vault.seal("hunter2", kp)
    assert b"hunter2" not in s.secret_ct and vault.unseal(s.secret_ct, s.dek_ct, kp) == "hunter2"
    s2 = vault.seal("hunter2", kp)
    assert s2.dek_ct != s.dek_ct                       # a fresh data key per secret
    with pytest.raises(InvalidToken):
        vault.unseal(s.secret_ct, s2.dek_ct, kp)       # wrong DEK
    with pytest.raises(InvalidToken):
        vault.unseal(s.secret_ct, s.dek_ct, vault.LocalKeyProvider(Fernet.generate_key().decode()))  # wrong master


# ------------------------------------------------------------------------------------------ onboarding
MESSY = """Pol #;Insured Name;DOL;Peril;Amount Claimed;What happened;Tel;E-mail;Source ID
123456;DOE, JANE;03/04/2026;rear ended;"$1,250.50";Hit at lights;+44 7700 900123;jane@example.com;A-1
pol 234567;bob smith;2026-02-11;Burst pipe;980;Kitchen flood;;bob@example;A-2
999;Ann Lee;31/02/2026;meteor;-5;;;;A-3
123456;DOE, JANE;03/04/2026;rear ended;"$1,250.50";Hit at lights;+44 7700 900123;jane@example.com;A-1
"""


def test_onboarding_maps_messy_headers_and_normalizes():
    mapping, rows = parse_csv(MESSY.encode(), "demo", "acme-claims", day_first=True)
    m = {d.source: d.target for d in mapping}
    assert m["Pol #"] == "policy_number" and m["DOL"] == "loss_date" and m["Peril"] == "loss_type"
    assert m["Amount Claimed"] == "amount" and m["Insured Name"] == "claimant_name" and m["Source ID"] == "external_id"
    r1, r2, r3, r4 = rows
    assert r1.valid and r1.payload == {
        "policy_number": "POL-123456", "claimant_name": "Jane Doe", "claimant_phone": "447700900123",
        "claimant_email": "jane@example.com", "loss_date": "2026-04-03", "loss_type": "Collision", "amount": 1250.5,
        "description": "Hit at lights", "external_id": "A-1"}
    assert any("ambiguous date" in w for w in r1.warnings)
    assert not r2.valid and r2.payload["loss_type"] == "Water damage"
    assert {e["field"] for e in r2.errors} == {"claimant_email"}
    assert {e["field"] for e in r3.errors} >= {"policy_number", "loss_date", "loss_type", "amount"}
    assert not r4.valid and "duplicate of row 1" in r4.errors[0]["message"]
    assert r1.idempotency_key.startswith("PP-") and r4.idempotency_key != r1.idempotency_key


def test_idempotency_key_is_stable_across_uploads():
    _, a = parse_csv(MESSY.encode(), "demo", "acme-claims")
    _, b = parse_csv(MESSY.encode(), "demo", "acme-claims")
    assert [r.idempotency_key for r in a] == [r.idempotency_key for r in b]


def test_parse_date_month_first():
    d, warn = parse_date("03/04/2026", day_first=False)
    assert d.isoformat() == "2026-03-04" and warn
