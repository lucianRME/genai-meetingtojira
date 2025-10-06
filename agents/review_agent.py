# agents/review_agent.py
from typing import Dict, Any, List
from agents.base import Agent
from schemas import dedupe_requirements, validate_requirement

class ReviewAgent(Agent):
    name = "review"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reqs: List[dict] = state.get("requirements", [])
        if not reqs:
            return {"requirements": []}
        cleaned = [validate_requirement(r) for r in reqs]
        cleaned = dedupe_requirements(cleaned)
        return {"requirements": cleaned}