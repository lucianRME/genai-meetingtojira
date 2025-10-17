🤖 Synapse — GenAI Requirements & BDD Generator (Agentic Framework)

🧠 Microsoft Teams Transcript → Structured Requirements + Test Cases → Review → Jira Sync


Synapse is an agentic AI proof-of-concept that automates requirement and test case generation from real meeting transcripts.
It demonstrates how GenAI can integrate across Agile delivery stages — from transcript ingestion to Jira synchronization — using modular multi-agent orchestration.

🌐 End-to-End Flow
Stage	Agent	Description
1️⃣	IngestAgent	Reads .vtt meeting transcript from Teams or OneDrive
2️⃣	RequirementAgent	Extracts 3–6 structured business requirements
3️⃣	ReviewAgent	Validates, deduplicates, and classifies requirements
4️⃣	TestAgent	Generates 3 BDD / Gherkin test cases per requirement
5️⃣	PersistAgent	Writes results to SQLite (repo.db) and JSON
6️⃣	Flask UI (app/)	Human approval and one-click Jira sync
7️⃣	JiraAgent	Idempotently syncs approved requirements and tests to Jira
🧱 Architecture (Agentic Controller)
flowchart TD
    A[Transcript .vtt/.txt] --> B[IngestAgent]
    B --> C[RequirementAgent]
    C --> D[ReviewAgent]
    D --> E[TestAgent]
    E --> F[PersistAgent]
    F --> G[(repo.db)]
    F --> H[output.json]
    G --> I[Flask UI - app/app.py<br>Approve + Sync]
    I --> J[Jira Cloud (idempotent sync)]
    F --> K[export_csv.py<br>CSV export]
    J --> L[Analytics & Reporting]

🔧 Setup
1. Environment

Create .env in project root:

OPENAI_API_KEY=sk-your-key
OPENAI_MODEL=gpt-4o
OPENAI_TEMPERATURE=0.2

# Jira
JIRA_URL=https://yourcompany.atlassian.net
JIRA_USER=you@example.com
JIRA_API_TOKEN=your-jira-token
JIRA_PROJECT=SCRUM

# Behaviour
PIPELINE_MODE=agentic
JIRA_SYNC_ON_PIPELINE=1
JIRA_APPROVED_ONLY=1


⚠️ Never commit .env — it’s excluded via .gitignore.

2. Install dependencies
pip install -r requirements.txt

3. Run full Agentic pipeline
python run_pipeline.py --mode agentic


Output:

🧩 Requirements: 4
✅ Test cases: 12
✅ Jira sync complete.
📦 outputs: output.json , repo.db

4. Launch the Review UI
python -m app.app


Then open http://127.0.0.1:5000/

✅ Approve requirements
✅ Sync approved items directly to Jira
✅ View effective project/session memory (tone, prefix, etc.)

🧠 Memory & Session Awareness

Synapse uses a lightweight memory store in SQLite:

Table	Purpose
memory_project	Project-level configuration (e.g., Jira prefixes, tone)
memory_session	Session-specific overrides
sessions	Execution trace for orchestration context

Example seeding:

INSERT OR REPLACE INTO memory_project(project_id,key,value)
VALUES
 ('primark','tone','Concise, British English'),
 ('primark','jira.story_prefix','PK');

📊 Example Output

Console

🚀 E2E DONE
🧩 requirements: 4
✅ test cases:    12
📦 outputs:       output.json , repo.db
🧭 project_id:    primark
🧾 session_id:    91b3e6aa...


Jira Cloud

Stories: PK-101, PK-102

Linked Test Tasks: PK-103–PK-108 via Relates links

🧩 Repository Layout
.
├── agents/
│   ├── agentic_controller.py     # Multi-agent orchestrator
│   ├── ingest_agent.py           # Transcript ingestion
│   ├── requirements_agent.py     # Requirement extraction
│   ├── review_agent.py           # Review & dedupe logic
│   ├── tests_agent.py            # BDD generation
│   ├── persist_agent.py          # DB persistence
│   └── jira_agent.py             # Jira sync (ADF idempotent)
│
├── app/
│   ├── app.py                    # Flask web UI
│   └── templates/
│
├── infra/memory.py               # Memory hydrator and trace store
├── schemas.py                    # Data validation helpers
├── run_pipeline.py               # E2E orchestrator
├── export_csv.py                 # CSV export
├── repo.db                       # SQLite store
├── output/                       # Generated CSVs
└── requirements.txt

🚀 Current Capabilities (v3)

✅ Full Agentic orchestration

✅ Persistent memory (project + session)

✅ Flask UI for review and Jira sync

✅ Deterministic, idempotent Jira integration

✅ Robust JSON recovery & chunking logic

✅ CSV + SQLite persistence

✅ Modular agent framework ready for enterprise scaling

🔮 Next Milestone (v4)

🧠 Chain-of-Thought traces + self-checks per agent

📈 Metrics dashboard in UI

☁️ Azure App Service / ACR container deployment

📂 OneDrive ingestion for auto transcript detection

🧭 Vision

Synapse illustrates how GenAI can act as an intelligent assistant for delivery teams —
converting raw conversation into structured, testable, and traceable artifacts
directly integrated with enterprise tools like Jira and Azure DevOps.