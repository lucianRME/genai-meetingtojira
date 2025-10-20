# agents/review_agent.py
from __future__ import annotations

from typing import Dict, Any, List

from agents.base import Agent
from schemas import dedupe_requirements, validate_requirement


class ReviewAgent(Agent):
    name = "review"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reqs: List[dict] = state.get("requirements", []) or []
        if not reqs:
            self.log(state, "review_skipped", reason="no_requirements")
            return {"requirements": []}

        before = len(reqs)
        self.log(state, "review_start", count=before)

        # Validate & dedupe
        cleaned = [validate_requirement(r) for r in reqs]
        cleaned = dedupe_requirements(cleaned)
        after = len(cleaned)

        # Session logs + compact summary
        self.log(state, "review_done", before=before, after=after, removed=before - after)
        if after != before:
            self.append_summary(state, f"Reviewed requirements; deduplicated to {after} items (from {before}).")
        else:
            self.append_summary(state, f"Reviewed requirements; {after} items (no duplicates removed).")

        # Expose for controller/UI bullets
        state["requirements_deduped_count"] = after

        return {"requirements": cleaned}