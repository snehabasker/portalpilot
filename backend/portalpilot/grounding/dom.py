"""Deterministic DOM tier: finds the element through Playwright's accessibility locators.

Not vision: it exists so the whole system (workflows, retries, double-submit protection, UI) can be tested in CI
and on machines with no GPU and no API key. In the vision chain it can also sit last as a non-visual fallback for
portals that expose a sane accessibility tree.
"""
from __future__ import annotations

from .base import Candidate, Observation, Target


class DomGrounder:
    name = "dom"

    async def locate(self, obs: Observation, target: Target, page) -> Candidate | None:
        if target.label:
            loc = page.get_by_label(target.label, exact=False)
        elif target.role:
            loc = page.get_by_role(target.role, name=target.expect, exact=False)
        else:
            loc = page.get_by_text(target.expect, exact=False)
        dialog = page.locator("[role=dialog]:not([hidden])")
        if await dialog.count():
            # mirror what a human sees: only the dialog is reachable while it is open
            inside = dialog.first.get_by_role(target.role or "button", name=target.expect, exact=False)
            loc = inside if await inside.count() else loc
        n = await loc.count()
        for i in range(n):
            box = await loc.nth(i).bounding_box()
            if box and box["width"] > 0 and await loc.nth(i).is_visible():
                return Candidate(round(box["x"] + box["width"] / 2), round(box["y"] + box["height"] / 2))
        return Candidate(0, 0, found=False, raw="no visible element matched")
