"""Data onboarding: messy client CSV -> validated, normalized claim rows with idempotency keys.

Header mapping is decided in three passes, cheapest first, and the decision for every column is recorded so a
human can review it before the batch runs:
  1. exact synonym match after normalizing case/punctuation
  2. token overlap with the synonyms
  3. (optional) Claude, for headers nothing else could place, with structured output
Rows that fail validation are kept as ``invalid`` tasks with field-level errors instead of being dropped.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

FIELDS: dict[str, list[str]] = {
    "policy_number": ["policy number", "policy no", "policy", "policy id", "pol no", "policy #", "policynum"],
    "claimant_name": ["claimant name", "claimant", "insured name", "name", "full name", "customer name", "policyholder"],
    "claimant_phone": ["phone", "phone number", "telephone", "mobile", "contact number", "tel"],
    "claimant_email": ["email", "email address", "e mail", "contact email"],
    "loss_date": ["date of loss", "loss date", "incident date", "date of incident", "dol", "accident date"],
    "loss_type": ["type of loss", "loss type", "claim type", "incident type", "peril", "cause of loss"],
    "amount": ["amount", "claim amount", "amount claimed", "estimate", "total", "loss amount", "value"],
    "description": ["description", "details", "notes", "narrative", "what happened", "loss description"],
    "external_id": ["external id", "row id", "ref", "reference", "client ref", "source id", "id"],
}
REQUIRED = ["policy_number", "claimant_name", "loss_date", "loss_type", "amount"]
LOSS_TYPES = {
    "Collision": ["collision", "crash", "accident", "car accident", "vehicle collision", "rear ended"],
    "Theft": ["theft", "stolen", "burglary", "robbery"],
    "Water damage": ["water", "water damage", "flood", "leak", "burst pipe"],
    "Fire": ["fire", "smoke", "burn"],
    "Other": ["other", "misc", "miscellaneous"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


@dataclass
class MappingDecision:
    source: str
    target: str | None
    method: str          # synonym | fuzzy | llm | unmapped
    confidence: float


@dataclass
class NormalizedRow:
    row_number: int
    raw: dict
    payload: dict
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    idempotency_key: str = ""

    @property
    def valid(self) -> bool:
        return not self.errors


def map_headers(headers: list[str], llm=None) -> list[MappingDecision]:
    out: list[MappingDecision] = []
    taken: set[str] = set()
    syn = {_norm(s): f for f, ss in FIELDS.items() for s in ss + [f]}
    for h in headers:
        f = syn.get(_norm(h))
        if f and f not in taken:
            out.append(MappingDecision(h, f, "synonym", 1.0))
            taken.add(f)
            continue
        best, score = None, 0.0
        toks = set(_norm(h).split())
        for f, ss in FIELDS.items():
            if f in taken:
                continue
            for s in ss:
                st = set(s.split())
                j = len(toks & st) / max(1, len(toks | st))
                if j > score:
                    best, score = f, j
        if best and score >= 0.5:
            out.append(MappingDecision(h, best, "fuzzy", round(score, 2)))
            taken.add(best)
        else:
            out.append(MappingDecision(h, None, "unmapped", 0.0))
    unmapped = [d for d in out if d.target is None]
    if llm and unmapped:
        free = [f for f in FIELDS if f not in taken]
        for src, tgt in llm(headers, [d.source for d in unmapped], free).items():
            d = next((d for d in unmapped if d.source == src), None)
            if d and tgt in free:
                d.target, d.method, d.confidence = tgt, "llm", 0.7
                free.remove(tgt)
    return out


def claude_header_mapper(model: str):
    """Returns a mapper that asks Claude to place headers the rules could not. Structured output, so the answer is
    always a valid JSON object; anything outside the allowed fields is discarded by the caller."""
    import anthropic

    client = anthropic.Anthropic()

    def mapper(all_headers: list[str], unmapped: list[str], free_fields: list[str]) -> dict[str, str | None]:
        schema = {"type": "object", "properties": {"mappings": {"type": "array", "items": {
            "type": "object", "properties": {"source": {"type": "string"}, "target": {"type": ["string", "null"]}},
            "required": ["source", "target"], "additionalProperties": False}}},
            "required": ["mappings"], "additionalProperties": False}
        resp = client.messages.create(
            model=model, max_tokens=2048, output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content":
                       f"A client sent an insurance-claims CSV with headers {all_headers}. Map each of these unplaced "
                       f"headers to one of the fields {free_fields}, or null if none fits: {unmapped}. Each field may "
                       "be used once."}])
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        try:
            return {m["source"]: m["target"] for m in json.loads(text).get("mappings", [])}
        except (json.JSONDecodeError, KeyError, TypeError):
            return {}

    return mapper


_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%b %d %Y", "%d %B %Y",
                 "%B %d %Y", "%Y/%m/%d", "%m/%d/%y", "%d/%m/%y"]


def parse_date(v: str, day_first: bool) -> tuple[date | None, str | None]:
    v = re.sub(r"\s+", " ", v.replace(",", " ")).strip()
    hits = []
    for fmt in _DATE_FORMATS:
        try:
            hits.append((fmt, datetime.strptime(v, fmt).date()))
        except ValueError:
            pass
    dates = {d for _, d in hits}
    if not dates:
        return None, None
    if len(dates) == 1:
        return dates.pop(), None
    pick = next((d for f, d in hits if f.startswith("%d") == day_first), sorted(dates)[0])
    return pick, f"ambiguous date {v!r}; read as {pick.isoformat()} ({'day' if day_first else 'month'} first)"


def normalize_row(n: int, raw: dict, mapping: list[MappingDecision], tenant: str, portal: str,
                  day_first: bool = True) -> NormalizedRow:
    row = NormalizedRow(n, raw, {})
    vals = {d.target: (raw.get(d.source) or "").strip() for d in mapping if d.target}
    err = lambda f, m: row.errors.append({"field": f, "message": m})  # noqa: E731

    for f in REQUIRED:
        if not vals.get(f):
            err(f, "missing")

    if pn := vals.get("policy_number"):
        digits = re.sub(r"\D", "", pn)
        if len(digits) == 6:
            row.payload["policy_number"] = f"POL-{digits}"
        else:
            err("policy_number", f"expected 6 digits, got {pn!r}")
    if nm := vals.get("claimant_name"):
        if "," in nm:  # "Doe, Jane" -> "Jane Doe"
            last, first = [p.strip() for p in nm.split(",", 1)]
            nm = f"{first} {last}"
        row.payload["claimant_name"] = " ".join(w.capitalize() if w.isupper() or w.islower() else w for w in nm.split())
    if ph := vals.get("claimant_phone"):
        d = re.sub(r"\D", "", ph)
        if 10 <= len(d) <= 13:
            row.payload["claimant_phone"] = d
        else:
            row.warnings.append(f"phone {ph!r} dropped: not 10-13 digits")
    if em := vals.get("claimant_email"):
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", em.lower()):
            row.payload["claimant_email"] = em.lower()
        else:
            err("claimant_email", f"invalid email {em!r}")
    if dl := vals.get("loss_date"):
        d, warn = parse_date(dl, day_first)
        if d is None:
            err("loss_date", f"unparseable date {dl!r}")
        elif d > date.today():
            err("loss_date", f"date of loss {d.isoformat()} is in the future")
        else:
            row.payload["loss_date"] = d.isoformat()
            if warn:
                row.warnings.append(warn)
    if lt := vals.get("loss_type"):
        key = _norm(lt)
        hit = next((canon for canon, syns in LOSS_TYPES.items() if key in syns or key == _norm(canon)), None)
        hit = hit or next((canon for canon, syns in LOSS_TYPES.items() if any(s in key for s in syns)), None)
        if hit:
            row.payload["loss_type"] = hit
        else:
            err("loss_type", f"unknown loss type {lt!r}")
    if am := vals.get("amount"):
        a = am.replace(",", "").replace("$", "").replace("£", "").replace("€", "").strip()
        neg = a.startswith("(") and a.endswith(")")
        try:
            v = float(a.strip("()"))
            if neg or v <= 0:
                err("amount", f"amount must be positive, got {am!r}")
            elif v > 1_000_000:
                err("amount", f"amount {v:,.2f} exceeds the portal limit")
            else:
                row.payload["amount"] = round(v, 2)
        except ValueError:
            err("amount", f"not a number: {am!r}")
    row.payload["description"] = vals.get("description", "")[:500]
    if ext := vals.get("external_id"):
        row.payload["external_id"] = ext

    identity = row.payload.get("external_id") or "|".join(
        str(row.payload.get(k, "")) for k in ("policy_number", "loss_date", "amount", "claimant_name"))
    row.idempotency_key = "PP-" + hashlib.sha256(f"{tenant}|{portal}|{identity}".encode()).hexdigest()[:10].upper()
    return row


def parse_csv(content: bytes, tenant: str, portal: str, day_first: bool = True, llm=None):
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    mapping = map_headers(headers, llm=llm)
    rows = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(reader, start=1):
        if not any((v or "").strip() for v in raw.values()):
            continue
        r = normalize_row(i, {k: v for k, v in raw.items() if k is not None}, mapping, tenant, portal, day_first)
        if r.idempotency_key in seen:
            r.errors.append({"field": "*", "message": f"duplicate of row {seen[r.idempotency_key]}"})
            r.idempotency_key += f"-DUP{i}"  # kept for review, never runnable
        else:
            seen[r.idempotency_key] = i
        rows.append(r)
    return mapping, rows
