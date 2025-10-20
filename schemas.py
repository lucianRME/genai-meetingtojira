# schemas.py
from __future__ import annotations
from typing import Dict, Any, List, Iterable, Tuple
import re

REQ_ID_RE = re.compile(r"^REQ-\d{3,}$")  # tolerant: REQ-001, REQ-0123, etc.
ALLOWED_PRIORITIES = {"high", "medium", "low", ""}
ALLOWED_SCENARIOS = {"positive", "negative", "regression"}

def _as_str(x: Any) -> str:
    return "" if x is None else str(x)

def _norm_priority(p: str) -> str:
    p = (_as_str(p).strip().lower())
    if p in {"h", "hi"}: p = "high"
    elif p in {"m", "med"}: p = "medium"
    elif p in {"l", "lo"}: p = "low"
    return p if p in ALLOWED_PRIORITIES else ""

def _norm_req_id(x: str) -> str:
    x = _as_str(x).strip().upper()
    # Allow common variants like "REQ-1" or "REQ1" and pad
    m = re.search(r"REQ-?(\d+)$", x)
    if not m:
        return _as_str(x)  # leave as-is (controller/enforcer may fix later)
    n = int(m.group(1))
    return f"REQ-{n:03d}"

def _ensure_three(items: List[str]) -> List[str]:
    items = [i for i in items if i]
    while len(items) < 3:
        items.append("TBD")
    return items[:3]

def _norm_tags(tags: Any) -> List[str]:
    if not tags:
        return []
    if isinstance(tags, str):
        # split on whitespace/commas
        parts = re.split(r"[,\s]+", tags)
    elif isinstance(tags, Iterable):
        parts = [str(t) for t in tags]
    else:
        parts = []
    out: List[str] = []
    for t in parts:
        t = t.strip()
        if not t:
            continue
        if not t.startswith("@"):
            t = "@" + t
        out.append(t.lower())
    # de-dup preserve order
    seen = set(); dedup = []
    for t in out:
        if t not in seen:
            seen.add(t); dedup.append(t)
    return dedup

def validate_requirement(r: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalises and minimally validates a requirement dict.
    - keeps id as provided but normalises to REQ-### when possible
    - acceptance_criteria is exactly 3 short items (pads with 'TBD')
    - priority normalised to high/medium/low or ''
    - epic/title/description guaranteed strings
    """
    r = dict(r or {})
    rid = _norm_req_id(r.get("id", ""))
    title = _as_str(r.get("title")).strip()
    description = _as_str(r.get("description")).strip()

    # acceptance criteria -> list[str] of len 3
    ac_raw = r.get("acceptance_criteria")
    if isinstance(ac_raw, str):
        # split on newlines or bullets
        parts = [p.strip("-• \t") for p in re.split(r"[\n\r]+", ac_raw) if p.strip()]
    else:
        parts = [ _as_str(x).strip() for x in (ac_raw or []) ]
    ac = _ensure_three([p for p in parts if p])

    priority = _norm_priority(r.get("priority", ""))
    epic = _as_str(r.get("epic")).strip()

    out = {
        **r,
        "id": rid,
        "title": title,
        "description": description,
        "acceptance_criteria": ac,
        "priority": priority,
        "epic": epic,
    }
    return out

def validate_test_case(t: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalises a test case dict and adds `gherkin_valid: bool`.
    - scenario_type ∈ {positive, negative, regression} (lowercased)
    - ensures tags include the scenario tag (e.g., @positive)
    - minimal Gherkin check: contains Scenario:, Given, When, Then
    """
    t = dict(t or {})
    req_id = _norm_req_id(t.get("requirement_id", ""))
    scenario = _as_str(t.get("scenario_type")).strip().lower()
    if scenario not in ALLOWED_SCENARIOS:
        # try soft-mapping common aliases
        if scenario in {"pos"}: scenario = "positive"
        elif scenario in {"neg"}: scenario = "negative"
        elif scenario in {"reg", "regress"}: scenario = "regression"
        else: scenario = _as_str(scenario)

    g = _as_str(t.get("gherkin")).strip()
    # minimal gherkin validity
    has_tokens = all(tok in g for tok in ("Scenario:", "Given", "When", "Then"))

    tags = _norm_tags(t.get("tags"))
    scenario_tag = f"@{scenario}" if scenario in ALLOWED_SCENARIOS else None
    if scenario_tag and scenario_tag not in tags:
        tags.append(scenario_tag)

    t["requirement_id"] = req_id
    t["scenario_type"] = scenario
    t["gherkin"] = g
    t["gherkin_valid"] = bool(has_tokens)
    t["tags"] = tags
    return t

def dedupe_requirements(reqs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    De-duplicate by (id if valid) else by (title, description) case-insensitive.
    Keeps the first occurrence (stable).
    """
    out: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for r in reqs:
        rid = _norm_req_id(r.get("id", ""))
        if REQ_ID_RE.match(rid):
            key = ("ID", rid)  # prioritise explicit IDs
        else:
            key = (
                _as_str(r.get("title")).strip().lower(),
                _as_str(r.get("description")).strip().lower(),
            )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out