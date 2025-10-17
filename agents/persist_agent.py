# agents/persist_agent.py
import os, json, sqlite3
from typing import Dict, Any
from agents.base import Agent
from generate_req_bdd import ensure_schema

class PersistAgent(Agent):
    name = "persist"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        all_lines = state.get("all_lines", [])
        kept = len(state.get("filtered_lines", []))
        dropped = len(state.get("dropped_lines", []))
        requirements = state.get("requirements", [])
        test_cases = state.get("test_cases", [])

        # JSON artifact
        os.makedirs(".", exist_ok=True)
        with open("output.json","w",encoding="utf-8") as f:
            json.dump({
                "filtering": {
                    "total_lines": len(all_lines),
                    "kept": kept,
                    "dropped": dropped,
                    "use_llm_classifier": False,
                },
                "requirements": requirements,
                "test_cases": test_cases
            }, f, indent=2, ensure_ascii=False)

        # DB upsert
        ensure_schema()
        conn = sqlite3.connect("repo.db")
        cur = conn.cursor()

        for r in requirements:
            cur.execute(
                "INSERT OR REPLACE INTO requirements (id,title,description,criteria,priority,epic,approved) "
                "VALUES (?,?,?,?,?,?,COALESCE((SELECT approved FROM requirements WHERE id=?),0))",
                (
                    r["id"], r.get("title",""), r.get("description",""),
                    "\n".join(r.get("acceptance_riteria", r.get("acceptance_criteria", []))),
                    r.get("priority",""), r.get("epic",""),
                    r["id"],
                )
            )

        for t in test_cases:
            cur.execute(
                "INSERT INTO test_cases (requirement_id,scenario_type,gherkin,tags) VALUES (?,?,?,?)",
                (
                    t.get("requirement_id",""),
                    t.get("scenario_type",""),
                    t.get("gherkin",""),
                    json.dumps(t.get("tags", []))
                )
            )

        conn.commit(); conn.close()
        return {"output_json": "output.json", "db_path": "repo.db"}
