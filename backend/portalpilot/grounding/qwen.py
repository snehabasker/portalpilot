"""Tier 1: the fine-tuned Qwen2-VL-2B click policy (ui-click-rl), served by vLLM's OpenAI-compatible API.

Prompt and output format are exactly the ones the policy was trained with (uiclick/model.py): image first, then the
instruction plus the coordinate request; answer "(x, y)" in 0-1000 space relative to the image. The parser is the
training reward's parser, so an answer the reward would have scored 0 is treated as "not found" here too.
"""
from __future__ import annotations

import base64
import re
import time

import httpx

from ..config import settings
from .base import Candidate, Observation, Target

PROMPT = ("{instruction}\nReply with only the click point as (x, y), where x and y are integers from 0 to 1000 "
          "giving the position relative to the image width and height.")

_SPECIAL = re.compile(r"<\|[^|>]*\|>")
_NUM = r"-?\d+(?:\.\d+)?"
_PAIR = re.compile(rf"\(?\s*({_NUM})\s*,\s*({_NUM})\s*\)?")


def parse_click(text: str) -> tuple[float, float] | None:
    """Exactly one (x, y) pair, no other numbers, both within [0, 1000]; else None."""
    text = _SPECIAL.sub(" ", text)
    pairs = _PAIR.findall(text)
    if len(pairs) != 1 or len(re.findall(_NUM, text)) != 2:
        return None
    x, y = float(pairs[0][0]), float(pairs[0][1])
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        return None
    return x, y


class QwenGrounder:
    name = "qwen"

    def __init__(self, url: str | None = None, model: str | None = None, timeout_s: float | None = None):
        s = settings()
        self.url = (url or s.qwen_url).rstrip("/")
        self.model = model or s.qwen_model
        self._client = httpx.AsyncClient(timeout=timeout_s or s.qwen_timeout_s)
        self._down_until = 0.0  # circuit breaker: skip the tier for a while after a connection failure

    async def locate(self, obs: Observation, target: Target, page=None) -> Candidate | None:
        if time.monotonic() < self._down_until:
            raise ConnectionError("qwen tier circuit open")
        b64 = base64.b64encode(obs.png).decode()
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 24,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": PROMPT.format(instruction=target.description)},
            ]}],
        }
        try:
            r = await self._client.post(f"{self.url}/v1/chat/completions", json=body)
            r.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            self._down_until = time.monotonic() + 30
            raise ConnectionError(f"qwen unreachable: {e}") from e
        text = r.json()["choices"][0]["message"]["content"] or ""
        pt = parse_click(text)
        if pt is None:
            return Candidate(0, 0, found=False, raw=f"unparseable answer: {text!r}")
        return Candidate(round(pt[0] / 1000 * obs.width), round(pt[1] / 1000 * obs.height), 0.0, raw=text)
