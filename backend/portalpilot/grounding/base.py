"""Types shared by every grounding tier."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Target:
    """What to act on, in words a vision model can use, plus the facts the verifier checks.

    ``description`` goes to the vision models. ``expect`` is a substring the accessible name of the element under
    the predicted point must contain, and ``kind`` the element type it must be; neither is ever used to *find*
    the element in vision tiers, only to check it.
    """
    description: str
    expect: str
    kind: str = "any"          # button | link | input | select | any
    role: str | None = None    # ARIA role, used only by the deterministic DOM tier
    label: str | None = None   # form label, used only by the DOM tier


@dataclass
class Observation:
    png: bytes
    width: int
    height: int
    dom_hash: str
    url: str
    screenshot_sha: str | None = None


@dataclass
class Candidate:
    x: int
    y: int
    cost_usd: float = 0.0
    raw: str | None = None
    found: bool = True     # False: the tier answered but had no usable point (cost still counts)


@dataclass
class Verification:
    ok: bool
    reason: str
    element: dict[str, Any] | None = None

    @property
    def bbox(self) -> dict | None:
        return (self.element or {}).get("bbox")


@dataclass
class TierAttempt:
    tier: str
    found: bool
    x: int | None = None
    y: int | None = None
    verified: bool | None = None
    reason: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Grounding:
    x: int
    y: int
    tier: str
    verification: Verification
    trace: list[TierAttempt] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return sum(a.cost_usd for a in self.trace)


class GroundingFailed(Exception):
    def __init__(self, target: Target, trace: list[TierAttempt]):
        self.target, self.trace = target, trace
        super().__init__(f"no tier could ground {target.description!r}: " +
                         "; ".join(f"{a.tier}: {a.reason}" for a in trace))


class Grounder(Protocol):
    name: str

    async def locate(self, obs: Observation, target: Target, page: Any) -> Candidate | None: ...
