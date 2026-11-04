"""Tiered grounding: cheapest tier first, every answer verified against the DOM, escalate on failure.

    cache  -> point from a previous success on a structurally identical page          (free, ~1 ms)
    qwen   -> fine-tuned 2B click policy on the local GPU                              (≈free, ~100-300 ms)
    claude -> frontier model, only for what the small model got wrong                  ($, ~2-5 s)
    dom    -> deterministic accessibility lookup (CI / no-GPU)                          (free)

A verified point from any non-cache tier is written to the cache, so the expensive tiers pay for a page once per
batch, not once per row.
"""
from __future__ import annotations

import hashlib
import logging
import time

from .. import db
from ..config import settings
from .base import Grounder, Grounding, GroundingFailed, Observation, Target, TierAttempt
from .verify import verify

log = logging.getLogger(__name__)


def _ihash(target: Target) -> str:
    return hashlib.sha256(f"{target.kind}|{target.description}".encode()).hexdigest()[:32]


class GroundingCache:
    async def get(self, dom_hash: str, target: Target) -> tuple[int, int] | None:
        row = await (await db.pool()).fetchrow(
            "SELECT point_x, point_y FROM grounding_cache WHERE dom_hash = $1 AND instruction_hash = $2 AND misses < 3",
            dom_hash, _ihash(target))
        return (row["point_x"], row["point_y"]) if row else None

    async def hit(self, dom_hash: str, target: Target, ok: bool) -> None:
        col = "hits = hits + 1, last_hit_at = now()" if ok else "misses = misses + 1"
        await (await db.pool()).execute(
            f"UPDATE grounding_cache SET {col} WHERE dom_hash = $1 AND instruction_hash = $2", dom_hash, _ihash(target))

    async def put(self, dom_hash: str, target: Target, x: int, y: int, tier: str) -> None:
        await (await db.pool()).execute(
            "INSERT INTO grounding_cache (dom_hash, instruction_hash, instruction, point_x, point_y, source_grounder) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (dom_hash, instruction_hash) DO UPDATE "
            "SET point_x = EXCLUDED.point_x, point_y = EXCLUDED.point_y, source_grounder = EXCLUDED.source_grounder, misses = 0",
            dom_hash, _ihash(target), target.description, x, y, tier)


def build_tiers(chain: tuple[str, ...]) -> dict[str, Grounder]:
    tiers: dict[str, Grounder] = {}
    for name in chain:
        if name == "qwen":
            from .qwen import QwenGrounder
            tiers[name] = QwenGrounder()
        elif name == "claude":
            from .claude import ClaudeGrounder
            tiers[name] = ClaudeGrounder()
        elif name == "dom":
            from .dom import DomGrounder
            tiers[name] = DomGrounder()
        elif name != "cache":
            raise ValueError(f"unknown grounder tier {name!r}")
    return tiers


class GroundingRouter:
    def __init__(self, chain: tuple[str, ...] | None = None, tiers: dict[str, Grounder] | None = None,
                 cache: GroundingCache | None = None, verifier=verify):
        self.chain = chain or settings().grounder_chain
        self.tiers = tiers if tiers is not None else build_tiers(self.chain)
        self.cache = cache if cache is not None else (GroundingCache() if "cache" in self.chain else None)
        self.verify = verifier

    async def ground(self, page, obs: Observation, target: Target) -> Grounding:
        trace: list[TierAttempt] = []
        for tier in self.chain:
            t0 = time.perf_counter()
            att = TierAttempt(tier=tier, found=False)
            trace.append(att)
            try:
                if tier == "cache":
                    pt = await self.cache.get(obs.dom_hash, target) if self.cache else None
                    if pt is None:
                        att.reason = "miss"
                        continue
                    x, y = pt
                else:
                    cand = await self.tiers[tier].locate(obs, target, page)
                    att.cost_usd = cand.cost_usd if cand else 0.0
                    if cand is None or not cand.found:
                        att.reason = (cand.raw if cand else None) or "not found"
                        continue
                    x, y = cand.x, cand.y
                att.found, att.x, att.y = True, x, y
                v = await self.verify(page, x, y, target)
                att.verified, att.reason = v.ok, v.reason
                if tier == "cache" and self.cache:
                    await self.cache.hit(obs.dom_hash, target, v.ok)
                if v.ok:
                    if tier != "cache" and self.cache:
                        await self.cache.put(obs.dom_hash, target, x, y, tier)
                    return Grounding(x, y, tier, v, trace)
            except Exception as e:  # a broken tier must never stop the chain
                att.reason = f"error: {type(e).__name__}: {e}"[:300]
                log.warning("grounder %s failed: %s", tier, e)
            finally:
                att.latency_ms = round((time.perf_counter() - t0) * 1000)
        raise GroundingFailed(target, trace)
