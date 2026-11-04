"""The verifier: the confidence signal for every grounding tier.

A vision model's point is trusted only if the live DOM under that pixel is the kind of element the step expects and
its accessible name contains the expected text. A modal covering the target, a near-miss onto a neighbouring
button, or a stale cached point all fail here, before anything is clicked.
"""
from __future__ import annotations

import hashlib
import re

from .base import Target, Verification

INSPECT_JS = """
([x, y]) => {
  const hit = document.elementFromPoint(x, y);
  if (!hit) return null;
  let el = hit.closest('button, a, input, select, textarea, label, [role=button], [role=link], [role=checkbox], [role=option]') || hit;
  if (el.tagName === 'LABEL' && el.control) el = el.control;
  const r = el.getBoundingClientRect();
  let name = el.getAttribute('aria-label') || '';
  if (!name && el.labels && el.labels.length) name = Array.from(el.labels).map(l => l.innerText).join(' ');
  if (!name && el.tagName === 'INPUT' && ['submit', 'button'].includes(el.type)) name = el.value;
  if (!name) name = (el.innerText || '').trim();
  if (!name) name = el.getAttribute('placeholder') || el.getAttribute('title') || '';
  const modal = document.querySelector('[role=dialog]:not([hidden])');
  return {
    tag: el.tagName.toLowerCase(), type: el.type || null, role: el.getAttribute('role'),
    name: name.replace(/\\s+/g, ' ').slice(0, 200),
    bbox: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
    disabled: !!el.disabled, in_modal: !!(modal && modal.contains(el)), modal_open: !!modal,
  };
}
"""

# Structural fingerprint of the page: interactive elements, headings and labels with their rounded positions, never
# field values. Two claims on the same form page share a hash; the login page and the claims list do not.
DOM_SIGNATURE_JS = """
() => {
  const parts = [location.pathname];
  for (const el of document.querySelectorAll('button, a, input, select, textarea, h1, h2, label, [role=dialog]')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const text = ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) ? (el.type || '') : (el.innerText || '').trim().slice(0, 40);
    parts.push([el.tagName, el.getAttribute('role') || '', text, Math.round(r.x / 8), Math.round(r.y / 8),
                Math.round(r.width / 8), Math.round(r.height / 8)].join(':'));
  }
  return parts.join('|');
}
"""

_KINDS = {
    "button": lambda e: e["tag"] == "button" or e["role"] == "button" or (e["tag"] == "input" and e["type"] in ("submit", "button")),
    "link": lambda e: e["tag"] == "a" or e["role"] == "link",
    "input": lambda e: e["tag"] in ("input", "textarea") and e["type"] not in ("submit", "button", "checkbox", "radio"),
    "select": lambda e: e["tag"] == "select",
    "any": lambda e: True,
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


async def dom_hash(page) -> str:
    return hashlib.sha256((await page.evaluate(DOM_SIGNATURE_JS)).encode()).hexdigest()[:32]


async def inspect(page, x: int, y: int) -> dict | None:
    return await page.evaluate(INSPECT_JS, [x, y])


async def verify(page, x: int, y: int, target: Target) -> Verification:
    el = await inspect(page, x, y)
    if el is None:
        return Verification(False, "nothing at that point")
    if el["modal_open"] and not el["in_modal"]:
        return Verification(False, "a dialog is covering the page", el)
    if el["disabled"]:
        return Verification(False, f"element is disabled ({el['name']!r})", el)
    if not _KINDS[target.kind](el):
        return Verification(False, f"expected a {target.kind}, hit <{el['tag']}> {el['name']!r}", el)
    if _norm(target.expect) not in _norm(el["name"]):
        return Verification(False, f"expected name containing {target.expect!r}, hit {el['name']!r}", el)
    return Verification(True, "ok", el)
