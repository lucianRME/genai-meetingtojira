# agentic_controller.py
from typing import Dict, Any, List, Callable
from agents.ingest_agent import IngestAgent
from agents.requirements_agent import RequirementAgent
from agents.review_agent import ReviewAgent
from agents.tests_agent import TestAgent
from agents.persist_agent import PersistAgent
import time

class Controller:
    def __init__(self, steps: List, on_step: Callable[[str, Dict[str, Any]], None] | None = None):
        self.steps = steps
        self.state: Dict[str, Any] = {}
        self.on_step = on_step

    def run(self, initial: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if initial: self.state.update(initial)
        t0 = time.perf_counter()
        for step in self.steps:
            out = step.run(self.state)
            self.state.update(out)
            if self.on_step:
                self.on_step(step.name, self.state)
        self.state["metrics"] = {
            "requirements_count": len(self.state.get("requirements", [])),
            "test_cases_count": len(self.state.get("test_cases", [])),
            "runtime_sec": round(time.perf_counter() - t0, 2),
        }
        return self.state

if __name__ == "__main__":
    def log(name, state):
        if name == "requirements":
            print(f"🧩 Requirements: {len(state.get('requirements', []))}")
        if name == "tests":
            print(f"✅ Test cases: {len(state.get('test_cases', []))}")

    flow = Controller([
        IngestAgent(),
        RequirementAgent(),
        ReviewAgent(),
        TestAgent(),
        PersistAgent(),
    ], on_step=log)

    result = flow.run({"transcript_path": None})  # or "meeting_transcript.vtt"
    print("📦 Artifacts:", result.get("output_json"), result.get("db_path"))
    print("⏱️ Metrics:", result.get("metrics"))