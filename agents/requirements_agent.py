# agents/requirements_agent.py
from typing import Dict, Any, List
import json
from agents.base import Agent
from generate_req_bdd import _chat, MODEL, TEMPERATURE, extract_json_forgiving, enforce_ids_and_ac
from schemas import validate_requirement

REQ_PROMPT = """
You are a senior business analyst. Extract 3–6 clear, testable business requirements from this transcript.
Each requirement must have:
- id (REQ-001, REQ-002, ...)
- title
- description
- acceptance_criteria: exactly 3 short bullets
- priority (High/Medium/Low)
- epic (string or empty)

Transcript:
{transcript}

Return JSON array only.
""".strip()

class RequirementAgent(Agent):
    name = "requirements"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        lines = state.get("filtered_lines") or []
        if not lines:
            return {"requirements": []}
        transcript = "\n".join(lines)
        resp = _chat([{"role":"user","content": REQ_PROMPT.format(transcript=transcript)}], model=MODEL, temperature=TEMPERATURE)
        raw = (resp.choices[0].message.content or "[]")
        reqs = extract_json_forgiving(raw)
        reqs = enforce_ids_and_ac(reqs)
        reqs = [validate_requirement(r) for r in reqs]
        return {"requirements": reqs}