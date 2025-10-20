# agents/tests_agent.py
from __future__ import annotations

from typing import Dict, Any, List
import os
import json

from agents.base import Agent
from generate_req_bdd import (
    _chat,
    MODEL,
    TEMPERATURE,
    extract_json_forgiving,
    normalize_gherkin,
)
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


def _is_offline() -> bool:
    key = (os.getenv("OPENAI_API_KEY") or "").strip().lower()
    return bool(os.getenv("PYTEST_CURRENT_TEST")) or key in ("", "dummy", "test")


def _offline_bdd_for_requirement(req: dict) -> List[dict]:
    rid = req.get("id") or "REQ-001"
    title = req.get("title") or "Untitled"
    feature = title.replace("\n", " ").strip() or "Feature"

    scenarios = [
        ("positive", "@positive", "valid context", "happy path", "expected outcome"),
        ("negative", "@negative", "invalid input", "the action", "a clear error"),
        ("regression", "@regression", "known stable state", "repeat the action", "previous behaviour remains"),
    ]

    out = []
    for scenario_type, tag, given, when, then in scenarios:
        gherkin = (
            f"Feature: {feature}\n"
            f"  {tag}\n"
            f"  Scenario: {scenario_type.capitalize()} flow\n"
            f"    Given {given}\n"
            f"    When {when}\n"
            f"    Then {then}\n"
        )
        out.append({
            "requirement_id": rid,
            "scenario_type": scenario_type,
            "gherkin": gherkin,
            "tags": [tag[1:]],  # store without '@', e.g. ["positive"]
        })
    return out


class TestAgent(Agent):
    name = "tests"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reqs = state.get("requirements", []) or []
        if not reqs:
            self.log(state, "tests_skipped", reason="no_requirements")
            return {"test_cases": []}

        # Start log
        self.log(state, "tests_start", requirements=len(reqs))

        # === OFFLINE deterministic branch (for pytest / no API key) ===
        if _is_offline():
            raw_cases: List[dict] = []
            for r in reqs:
                raw_cases.extend(_offline_bdd_for_requirement(r))
        else:
            # === Normal LLM path ===
            prompt_raw = BDD_PROMPT.format(requirements=json.dumps(reqs, ensure_ascii=False, indent=2))
            prompt = self.build_prompt(state, prompt_raw)
            resp = _chat([{"role": "user", "content": prompt}], model=MODEL, temperature=TEMPERATURE)
            content = (resp.choices[0].message.content or "[]")
            raw_cases = extract_json_forgiving(content)

        # Normalize, validate
        out: List[dict] = []
        for t in raw_cases:
            t["gherkin"] = normalize_gherkin(t.get("gherkin", ""))
            t = validate_test_case(t)
            if t.get("gherkin_valid", True):  # validator may set this
                out.append(t)

        n_valid = len(out)
        self.log(state, "tests_done", total=len(raw_cases), valid=n_valid)
        self.append_summary(state, f"Generated {n_valid} valid BDD test cases from {len(reqs)} requirements.")
        state["test_cases_count"] = n_valid

        return {"test_cases": out}