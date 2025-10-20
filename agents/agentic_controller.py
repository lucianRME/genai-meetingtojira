# agents/agentic_controller.py
"""
Agentic controller for Synapse – orchestrates the pipeline steps (ingest → reqs → review → tests → persist).
Use from run_pipeline.py as:
    from agents.agentic_controller import Controller as AgenticController
"""

from __future__ import annotations

from typing import Dict, Any, List, Callable, Optional
import time

# Relative imports from the same package
from .ingest_agent import IngestAgent
from .requirements_agent import RequirementAgent
from .review_agent import ReviewAgent
from .tests_agent import TestAgent
from .persist_agent import PersistAgent

# Try to import session helpers (graceful no-op if unavailable in CI)
try:
    from app.session_manager import log_action as _sm_log_action, update_summary as _sm_update_summary
except Exception:
    def _sm_log_action(session_id: str, action_type: str, payload: Dict[str, Any] | None = None):
        return None
    def _sm_update_summary(session_id: str, bullet: str):
        return None

__all__ = ["Controller"]


def _step_name(step_obj: Any) -> str:
    return getattr(step_obj, "name", step_obj.__class__.__name__).lower()


class Controller:
    """
    Minimal agentic orchestration with session-awareness:
      - keeps a shared `state` dict
      - runs each step in order, merging outputs into state
      - logs per-step actions into memory_action and appends compact bullets to rolling summary
      - optional `on_step(name, state)` callback for live logs/metrics
    """

    def __init__(
        self,
        steps: List[Any] | None = None,
        on_step: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ):
        self.steps = steps or [
            IngestAgent(),
            RequirementAgent(),
            ReviewAgent(),
            TestAgent(),
            PersistAgent(),
        ]
        self.state: Dict[str, Any] = {}
        self.on_step = on_step

    def run(self, initial: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if initial:
            self.state.update(initial)

        session_id: Optional[str] = self.state.get("session_id")
        mode = (self.state.get("mode") or "agentic").lower()

        t0 = time.perf_counter()

        # Log pipeline start
        if session_id:
            _sm_log_action(session_id, "pipeline_start", {"mode": mode})
            _sm_update_summary(session_id, f"Started agentic run (mode: {mode}).")

        # Track counts to craft good bullets
        req_count_before = len(self.state.get("requirements", []))
        test_count_before = len(self.state.get("test_cases", []))

        for step in self.steps:
            name = _step_name(step)

            if session_id:
                _sm_log_action(session_id, f"{name}_start", {"mode": mode})

            # Execute the step
            out = step.run(self.state)

            # Merge outputs
            if out:
                self.state.update(out)

            # Optional external on_step hook (for CLI/UI)
            if self.on_step:
                self.on_step(name, self.state)

            # After-step logging + small, meaningful summary bullet
            if session_id:
                # compute interesting metrics if present
                reqs = self.state.get("requirements", [])
                tests = self.state.get("test_cases", [])
                bullet = None

                if name in ("ingest", "ingestagent"):
                    src = self.state.get("transcript_path") or self.state.get("transcript_name") or "transcript"
                    bullet = f"Ingested {src} and applied small-talk filtering."

                elif name in ("requirements", "requirementagent"):
                    req_n = len(reqs)
                    bullet = f"Extracted {req_n} business requirements from transcript."

                elif name in ("review", "reviewagent"):
                    req_n = len(reqs)
                    dedup = self.state.get("requirements_deduped_count")
                    if dedup is not None:
                        bullet = f"Reviewed requirements; deduplicated to {req_n} items."
                    else:
                        bullet = f"Reviewed and refined requirements ({req_n} items)."

                elif name in ("tests", "testagent"):
                    tc_n = len(tests)
                    bullet = f"Generated {tc_n} BDD test cases from requirements."

                elif name in ("persist", "persistagent"):
                    db_path = self.state.get("db_path") or "repo.db"
                    out_json = self.state.get("output_json")
                    bullet = f"Persisted outputs to DB ({db_path}) and JSON ({out_json or 'output.json'})."

                # Log done + update summary
                _sm_log_action(session_id, f"{name}_done", {
                    "requirements_count": len(self.state.get("requirements", [])),
                    "test_cases_count": len(self.state.get("test_cases", [])),
                })
                if bullet:
                    _sm_update_summary(session_id, bullet)

        # Final metrics
        metrics = {
            "requirements_count": len(self.state.get("requirements", [])),
            "test_cases_count": len(self.state.get("test_cases", [])),
            "runtime_sec": round(time.perf_counter() - t0, 2),
        }
        self.state["metrics"] = metrics

        # Final summary + log
        if session_id:
            _sm_log_action(session_id, "pipeline_done", metrics)
            _sm_update_summary(
                session_id,
                f"E2E complete: {metrics['requirements_count']} requirements, "
                f"{metrics['test_cases_count']} tests in {metrics['runtime_sec']}s."
            )

        return self.state


if __name__ == "__main__":
    # Quick manual run for smoke testing the agentic flow
    def log(name: str, state: Dict[str, Any]) -> None:
        if name.lower() in ("requirements", "requirementagent"):
            print(f"🧩 Requirements: {len(state.get('requirements', []))}")
        if name.lower() in ("tests", "testagent"):
            print(f"✅ Test cases: {len(state.get('test_cases', []))}")

    flow = Controller(on_step=log)

    # Set a transcript path here if you want to run it end-to-end
    result = flow.run({
        "transcript_path": None,  # e.g., "meeting_transcript.vtt"
        # "session_id": "your-session-id",  # pass this when launched from Flask / UI
    })
    print("📦 Artifacts:", result.get("output_json"), result.get("db_path"))
    print("⏱️ Metrics:", result.get("metrics"))