"""Tier 2: Claude as the expensive, more capable fallback grounder.

Gets the same screenshot plus the natural-language target and returns pixel coordinates through structured output,
so there is no free-text parsing. Called only when the cheap tiers failed verification.
"""
from __future__ import annotations

import base64
import json

import anthropic

from ..config import settings
from .base import Candidate, Observation, Target

# $ per token for claude-opus-5-5 (input $4 / MTok, output $20 / MTok)
PRICES = {"claude-opus-5-5": (4e-6, 20e-6), "claude-sonnet-5-5": (2e-6, 10e-6), "claude-haiku-5-5": (1e-7, 5e-7)}

SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["found", "x", "y", "reason"],
    "additionalProperties": False,
}

SYSTEM = (
    "You locate UI elements in screenshots of web applications for an automation agent. "
    "Coordinates are pixels in the screenshot as given, origin top-left. Return the centre of the single element "
    "the instruction refers to. If a dialog or overlay covers the element, or the element is not on screen, set "
    "found to false, x and y to 0, and say why in reason. Never guess an element that only resembles the target."
)


class ClaudeGrounder:
    name = "claude"

    def __init__(self, model: str | None = None):
        self.model = model or settings().claude_model
        self._client = anthropic.AsyncAnthropic(max_retries=3)

    async def locate(self, obs: Observation, target: Target, page=None) -> Candidate | None:
        resp = await self._client.beta.messages.create(
            model=self.model,
            max_tokens=2048,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.standard_b64encode(obs.png).decode()}},
                {"type": "text", "text": f"Screenshot is {obs.width}x{obs.height} px.\nInstruction: {target.description}"},
            ]}],
        )
        cin, cout = PRICES.get(self.model, (4e-6, 20e-6))
        u = resp.usage
        cost = (u.input_tokens + (u.cache_creation_input_tokens or 0)) * cin + (u.cache_read_input_tokens or 0) * cin * 0.1 \
            + u.output_tokens * cout
        if resp.stop_reason == "refusal":
            return Candidate(0, 0, cost, raw="refusal", found=False)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            return Candidate(0, 0, cost, raw=f"invalid JSON (stop_reason={resp.stop_reason})", found=False)
        x, y = int(out["x"]), int(out["y"])
        if not out.get("found") or not (0 <= x < obs.width and 0 <= y < obs.height):
            return Candidate(0, 0, cost, raw=out.get("reason") or "not found", found=False)
        return Candidate(x, y, cost_usd=cost, raw=out.get("reason"))
