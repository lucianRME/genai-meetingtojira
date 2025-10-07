# agents/agentic_controller.py
"""
Agentic controller for FlowMind – orchestrates the pipeline steps (ingest → reqs → review → tests → persist).
Use from run_pipeline.py as:
    from agents.agentic_controller import Controller as AgenticController
"""

from typing import Dict, Any, List, Callable, Optional
import time

# Relative imports from the same package
from .ingest_agent import IngestAgent
from .requirements_agent import RequirementAgent
from .review_agent import ReviewAgent
from .tests_agent import TestAgent
from .persist_agent import PersistAgent

__all__ = ["Controller"]


class Controller:
    """
    Minimal agentic orchestration:
      - keeps a shared `state` dict
      - runs each step in order, merging outputs into state
      - optional `on_step(name, state)` callback for live logs/metrics
    """

    def __init__(
        self,
        steps: List[Any],
        on_step: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ):
        self.steps = steps
        self.state: Dict[str, Any] = {}
        self.on_step = on_step

    def run(self, initial: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if initial:
            self.state.update(initial)

        t0 = time.perf_counter()

        for step in self.steps:
            out = step.run(self.state)
            if out:
                self.state.update(out)
            if self.on_step:
                # Step objects are expected to expose a `.name` attribute
                name = getattr(step, "name", step.__class__.__name__)
                self.on_step(name, self.state)

        self.state["metrics"] = {
            "requirements_count": len(self.state.get("requirements", [])),
            "test_cases_count": len(self.state.get("test_cases", [])),
            "runtime_sec": round(time.perf_counter() - t0, 2),
        }
        return self.state


if __name__ == "__main__":
    # Quick manual run for smoke testing the agentic flow
    def log(name: str, state: Dict[str, Any]) -> None:
        if name.lower() in ("requirements", "requirementagent"):
            print(f"🧩 Requirements: {len(state.get('requirements', []))}")
        if name.lower() in ("tests", "testagent"):
            print(f"✅ Test cases: {len(state.get('test_cases', []))}")

    flow = Controller(
        steps=[
            IngestAgent(),
            RequirementAgent(),
            ReviewAgent(),
            TestAgent(),
            PersistAgent(),
        ],
        on_step=log,
    )

    # Set a transcript path here if you want to run it end-to-end
    result = flow.run({"transcript_path": None})  # e.g., "meeting_transcript.vtt"
    print("📦 Artifacts:", result.get("output_json"), result.get("db_path"))
    print("⏱️ Metrics:", result.get("metrics"))