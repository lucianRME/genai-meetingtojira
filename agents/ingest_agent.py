# agents/ingest_agent.py
from typing import Dict, Any
from agents.base import Agent
from generate_req_bdd import read_vtt_lines, filter_transcript_lines, TRANSCRIPT_FILE, SMALLTALK_FILTER
import os

class IngestAgent(Agent):
    name = "ingest"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        path = state.get("transcript_path") or TRANSCRIPT_FILE
        if not os.path.exists(path):
            raise FileNotFoundError(f"Transcript not found: {path}")
        all_lines = read_vtt_lines(path)
        if SMALLTALK_FILTER:
            kept, dropped = filter_transcript_lines(all_lines)
        else:
            kept, dropped = all_lines, []
        return {
            "all_lines": all_lines,
            "filtered_lines": kept,
            "dropped_lines": dropped,
        }