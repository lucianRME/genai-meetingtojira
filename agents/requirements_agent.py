# agents/requirements_agent.py
from typing import Dict, Any, List
import json
from agents.base import Agent
from generate_req_bdd import _chat, MODEL, TEMPERATURE, extract_json_forgiving, enforce_ids_and_ac
from schemas import validate_requirement
from infra.memory import prompt_hydrator  # NEW

# This is the user prompt (task). Guidance like tone/format/jira prefix
# will come from the Memory block injected by prompt_hydrator() as a SYSTEM message.
REQ_PROMPT = """
You are a senior business analyst.
Extract 3–6 clear, testable business requirements from the transcript.
Each requirement MUST include:
- id (REQ-001, REQ-002, ...)
- title
- description
- acceptance_criteria: exactly 3 short bullets (Given/When/Then phrasing where possible)
- priority (High/Medium/Low)
- epic (string or empty)

Return a JSON array only.
Transcript:
{transcript}
""".strip()

class RequirementAgent(Agent):
    name = "requirements"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Inputs expected in state:
          - filtered_lines: List[str]
          - conn: sqlite3.Connection  (for Memory)
          - project_id: str
          - session_id: str
          - rag_context: Optional[str]  (future: RAG injection)
        """
        lines = state.get("filtered_lines") or []
        if not lines:
            return {"requirements": []}

        transcript = "\n".join(lines)

        # --- NEW: Build a hydrated SYSTEM prompt with Memory + Session summary (if present)
        conn = state.get("conn")
        project_id = state.get("project_id")
        session_id = state.get("session_id")
        rag_ctx = state.get("rag_context") or ""  # safe to be empty now

        base_system = (
            "You are the Requirements Agent. Follow the [Memory] settings for tone/format "
            "(e.g., British English, BDD rules). Produce Jira-ready requirements."
        )
        system_prompt = prompt_hydrator(
            conn,
            base_system_prompt=base_system,
            project_id=project_id,
            session_id=session_id,
            extra_ctx=rag_ctx
        )

        # Compose user message with the actual task + transcript
        user_prompt = REQ_PROMPT.format(transcript=transcript)

        # Call the LLM with SYSTEM + USER
        resp = _chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=MODEL,
            temperature=TEMPERATURE,
        )

        raw = (resp.choices[0].message.content or "[]")
        reqs = extract_json_forgiving(raw)
        reqs = enforce_ids_and_ac(reqs)
        reqs = [validate_requirement(r) for r in reqs]
        return {"requirements": reqs}