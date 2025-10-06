# schemas.py
from typing import Dict, Any, List

def validate_requirement(r: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(r or {})
    r.setdefault("id", "")
    r.setdefault("title", "")
    r.setdefault("description", "")
    ac = r.get("acceptance_criteria") or []
    ac = [str(x).strip() for x in ac if str(x).strip()]
    while len(ac) < 3: ac.append("TBD")
    if len(ac) > 3: ac = ac[:3]
    r["acceptance_criteria"] = ac
    r["priority"] = (r.get("priority") or "").strip()
    r["epic"] = (r.get("epic") or "").strip()
    return r

def validate_test_case(t: Dict[str, Any]) -> Dict[str, Any]:
    t = dict(t or {})
    t.setdefault("requirement_id", "")
    t.setdefault("scenario_type", "")
    g = (t.get("gherkin") or "").strip()
    # must include Scenario:, Given, When, Then
    ok = all(tok in g for tok in ("Scenario:", "Given", "When", "Then"))
    t["gherkin_valid"] = bool(ok)
    t["tags"] = t.get("tags") or []
    return t

def dedupe_requirements(reqs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in reqs:
        key = (r.get("title","").strip().lower(), r.get("description","").strip().lower())
        if key in seen: 
            continue
        seen.add(key)
        out.append(r)
    return out