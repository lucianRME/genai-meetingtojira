# agents/ingest_agent.py
from __future__ import annotations

from typing import Dict, Any
from pathlib import Path
import os

from agents.base import Agent
from generate_req_bdd import (
    read_vtt_lines,
    filter_transcript_lines,
    TRANSCRIPT_FILE,
    SMALLTALK_FILTER,
)


class IngestAgent(Agent):
    name = "ingest"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # Determine transcript path
        path = state.get("transcript_path") or TRANSCRIPT_FILE
        if not path or not os.path.exists(path):
            # Log and raise so controller error handling surfaces it
            self.log(state, "ingest_file_missing", path=path)
            raise FileNotFoundError(f"Transcript not found: {path}")

        # Persist for UI preview & later steps
        self.set_kv(state, "last_transcript_path", path)

        # Start log
        self.log(state, "ingest_start", path=path)

        # Read & (optionally) filter
        all_lines = read_vtt_lines(path)
        if SMALLTALK_FILTER:
            kept, dropped = filter_transcript_lines(all_lines)
        else:
            kept, dropped = all_lines, []

        kept_n = len(kept)
        drop_n = len(dropped)
        total_n = len(all_lines)
        fname = Path(path).name

        # Append a compact rolling-summary bullet
        if SMALLTALK_FILTER:
            bullet = f"Ingested {fname}: kept {kept_n}/{total_n} lines; dropped {drop_n} small-talk."
        else:
            bullet = f"Ingested {fname}: {kept_n} lines (no small-talk filter)."
        self.append_summary(state, bullet)

        # Done log with metrics
        self.log(
            state,
            "ingest_done",
            file=fname,
            total=total_n,
            kept=kept_n,
            dropped=drop_n,
            smalltalk_filter=bool(SMALLTALK_FILTER),
        )

        return {
            "transcript_path": path,
            "transcript_name": fname,
            "all_lines": all_lines,
            "filtered_lines": kept,
            "dropped_lines": dropped,
        }