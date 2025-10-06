# agents/tests_agent.py
from typing import Dict, Any, List
import json
from agents.base import Agent
from generate_req_bdd import _chat, MODEL, TEMPERATURE, extract_json_forgiving, normalize_gherkin, validate_gherkin
from schemas import validate_test_case

BDD_PROMPT = """
You are a QA engineer. For each requirement below, generate 3 scenarios in Gherkin:
- one "positive"
- one "negative"
- one "regression"
Rules:
- Start with 'Scenario:'
- Include at least one Given, one When, one Then
- Short, simple sentences
- Include tags: @positive, @negative, @regression

Return JSON array only with:
- requirement_id
- scenario_type
- gherkin
- tags (array)

Requirements:
{requirements}
""".strip()

class TestAgent(Agent):
    name = "tests"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reqs = state.get("requirements", [])
        if not reqs:
            return {"test_cases": []}
        prompt = BDD_PROMPT.format(requirements=json.dumps(reqs, ensure_ascii=False, indent=2))
        resp = _chat([{"role":"user","content": prompt}], model=MODEL, temperature=TEMPERATURE)
        raw = (resp.choices[0].message.content or "[]")
        tcs = extract_json_forgiving(raw)
        out: List[dict] = []
        for t in tcs:
            t["gherkin"] = normalize_gherkin(t.get("gherkin",""))
            t = validate_test_case(t)
            if t["gherkin_valid"]:
                out.append(t)
        return {"test_cases": out}