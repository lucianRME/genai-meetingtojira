# tests/test_schemas.py
from schemas import validate_requirement, validate_test_case, dedupe_requirements

def test_validate_requirement_normalises():
    r = validate_requirement({"id":"req-1","priority":"H","acceptance_criteria":["A","B"]})
    assert r["id"] == "REQ-001"
    assert r["priority"] in {"high","High","HIGH"} or r["priority"] == "high"  # depending on your version
    assert len(r["acceptance_criteria"]) == 3

def test_validate_test_case_enforces_tags():
    t = validate_test_case({"requirement_id":"REQ-1","scenario_type":"pos",
                            "gherkin":"Scenario: X\nGiven a\nWhen b\nThen c"})
    assert t["requirement_id"] == "REQ-001"
    assert t["gherkin_valid"] is True
    assert "@positive" in t["tags"]

def test_dedupe_prefers_ids():
    a = {"id":"REQ-001", "title":"t", "description":"d"}
    b = {"id":"REQ-001", "title":"t", "description":"d"}
    out = dedupe_requirements([a,b])
    assert len(out) == 1