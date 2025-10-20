Synapse — GenAI Requirements & BDD Generator (Agentic Framework)

Microsoft Teams transcript → structured requirements → BDD test cases → review UI → Jira sync

Overview
Synapse is an agentic AI proof-of-concept that converts real meeting transcripts (.vtt/.txt) into Jira-ready requirements and BDD tests. It uses a modular multi-agent pipeline, a lightweight memory layer for continuity, a Flask UI for human approval, and an idempotent Jira sync.

End-to-End Flow
1) IngestAgent        – Read transcript (.vtt/.txt)
2) RequirementAgent   – Extract 3–6 testable business requirements
3) ReviewAgent        – Validate/dedupe/classify
4) TestAgent          – Generate 3 BDD/Gherkin scenarios per requirement
5) PersistAgent       – Save to SQLite (repo.db) + JSON
6) Review UI (Flask)  – Approve + one-click sync to Jira
7) JiraAgent          – Idempotent sync (Stories + linked Tasks)

Quick Start
1) Environment
   Create a .env in repo root (do not commit):
     OPENAI_API_KEY=sk-your-key
     OPENAI_MODEL=gpt-4o
     OPENAI_TEMPERATURE=0.2

     # Jira (Cloud)
     JIRA_URL=https://yourcompany.atlassian.net
     JIRA_USER=you@example.com
     JIRA_API_TOKEN=your-jira-token
     JIRA_PROJECT=SCRUM

     # Behavior
     PROJECT_ID=primark
     PIPELINE_MODE=agentic
     JIRA_SYNC_ON_PIPELINE=1
     JIRA_APPROVED_ONLY=1
     JIRA_CREATE_LINKS=1

2) Install
   pip install -r requirements.txt

3) Run the agentic pipeline
   python run_pipeline.py --mode agentic --transcript meeting_transcript.vtt
   Outputs: output.json, repo.db
   Console shows counts (requirements/tests) and Jira sync status.

4) Launch the Review UI
   python -m app.app
   Open http://127.0.0.1:5000/
   • Approve requirements
   • Click “Sync to Jira”
   • See effective memory (tone/prefix) and recent actions

Memory & Session (SQLite)
- memory_project: project-level settings (tone, jira prefixes, guard hashes)
- memory_session: session context (rolling summary, last transcript summary)
- memory_action: structured action log (for recent actions UI)
- sessions: legacy snapshot (rolling summary + last_actions_json mirror)

Example seed (optional):
INSERT OR REPLACE INTO memory_project(project_id,key,value) VALUES
 ('primark','tone','Concise, British English'),
 ('primark','jira.story_prefix','PK');

Idempotency & Jira
- Requirements/Tests carry content hashes (memory_project) to skip unchanged updates.
- Tests also reuse the last known jira_key for (requirement_id, scenario_type) to update the same Task across runs, even if JQL search is restricted.
- Set JIRA_CREATE_LINKS=1 to link Tasks ↔ Stories (Relates).

Repository Layout
agents/
  agentic_controller.py  ingest_agent.py  requirements_agent.py
  review_agent.py        tests_agent.py   persist_agent.py  jira_agent.py
app/
  app.py + templates/ (Review UI)
infra/memory.py          schemas.py
run_pipeline.py          export_csv.py
repo.db (generated)      output/ (CSVs)
requirements.txt

Common Commands
# Full run (agentic)
python run_pipeline.py --mode agentic --transcript meeting_transcript.vtt

# Run without Jira
JIRA_SYNC_ON_PIPELINE=0 python run_pipeline.py --mode agentic --transcript meeting_transcript.vtt

# Start UI
python -m app.app

What You Get
- Multi-agent orchestration (deterministic prompts)
- Persistent project/session memory and action history
- Human-in-the-loop approval UI
- Idempotent Jira integration (Stories + BDD Tasks + optional links)
- JSON/CSV/SQLite outputs ready for analytics

Notes
- Ensure your Jira token/user can Browse, Create, Edit, and Link in the target project.
- Keep .env out of source control.
