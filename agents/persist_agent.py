# agents/persist_agent.py
from __future__ import annotations

import os, json, sqlite3
from pathlib import Path
from typing import Dict, Any, List

from agents.base import Agent
from generate_req_bdd import ensure_schema


def _as_text_lines(v) -> List[str]:
    """Normalize acceptance criteria to a list[str]."""
    if v is None:
        return []
    if isinstance(v, str):
        # split on newlines if a single blob
        return [s for s in (x.strip() for x in v.splitlines()) if s]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if str(x).strip()]
    # fallback
    return [str(v)]


class PersistAgent(Agent):
    name = "persist"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # Input collections
        all_lines = state.get("all_lines", []) or []
        kept = len(state.get("filtered_lines", []) or [])
        dropped = len(state.get("dropped_lines", []) or [])
        requirements = state.get("requirements", []) or []
        test_cases = state.get("test_cases", []) or []

        # Where to write artifacts
        out_json = "output.json"
        db_path = "repo.db"

        # JSON artifact
        os.makedirs(".", exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "filtering": {
                        "total_lines": len(all_lines),
                        "kept": kept,
                        "dropped": dropped,
                        "use_llm_classifier": False,
                    },
                    "requirements": requirements,
                    "test_cases": test_cases,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        # Ensure DB schema present
        ensure_schema()

        # DB upsert
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Upsert requirements (preserving existing 'approved' when present)
        for r in requirements:
            acc = r.get("acceptance_criteria", r.get("acceptance_riteria"))  # support legacy typo
            acc_text = "\n".join(_as_text_lines(acc))
            cur.execute(
                """
                INSERT OR REPLACE INTO requirements
                    (id, title, description, criteria, priority, epic, approved, jira_key)
                VALUES
                    (
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        COALESCE((SELECT approved FROM requirements WHERE id=?), 0),
                        COALESCE((SELECT jira_key  FROM requirements WHERE id=?), NULL)
                    )
                """,
                (
                    r.get("id"),
                    r.get("title", ""),
                    r.get("description", ""),
                    acc_text,
                    r.get("priority", ""),
                    r.get("epic", ""),
                    r.get("id"),
                    r.get("id"),
                ),
            )

        # Insert test cases (append-only; latest row per req_id+scenario is used downstream)
        for t in test_cases:
            cur.execute(
                "INSERT INTO test_cases (requirement_id, scenario_type, gherkin, tags) VALUES (?,?,?,?)",
                (
                    t.get("requirement_id", ""),
                    t.get("scenario_type", ""),
                    t.get("gherkin", ""),
                    json.dumps(t.get("tags", [])),
                ),
            )

        conn.commit()
        conn.close()

        # Session-aware: store small state for UI resume
        self.set_kv(state, "last_db_path", db_path)
        self.set_kv(state, "last_output_json", out_json)

        # Session logs + compact summary
        self.log(
            state,
            "persist_done",
            requirements=len(requirements),
            tests=len(test_cases),
            db=db_path,
            json=out_json,
        )

        fname_json = Path(out_json).name
        fname_db = Path(db_path).name
        self.append_summary(
            state,
            f"Persisted {len(requirements)} requirements and {len(test_cases)} tests → {fname_db} & {fname_json}.",
        )

        return {"output_json": out_json, "db_path": db_path}