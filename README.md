# 🤖 GenAI Requirements & BDD Generator (POC)

> 📝 **Meeting Transcript → Requirements + Test Cases → Approve → Export to Jira**

This repository is a **Proof of Concept (POC)** showing how **GenAI can automate requirement engineering and QA** — converting real meeting transcripts (e.g., from Microsoft Teams) into:

- 📋 **Requirements (user stories)**
- ✅ **BDD / Gherkin test cases**
- 🖥️ **Manual approval UI**
- 📤 **CSV export for Jira**

---

## 🌐 Overview

This POC demonstrates an **end-to-end (E2E)** AI-driven flow from raw meeting data to testable, traceable requirements — using OpenAI models and structured persistence.

| Step | Description |
|------|--------------|
| 1️⃣ | Ingests `.vtt` meeting transcripts |
| 2️⃣ | Filters small-talk and irrelevant lines (rule-based + optional LLM classifier) |
| 3️⃣ | Extracts structured business requirements |
| 4️⃣ | Generates **3 BDD test cases per requirement** (positive, negative, regression) |
| 5️⃣ | Persists results to **SQLite (repo.db)** and **JSON (output.json)** |
| 6️⃣ | Optionally exports to **CSV → Jira import** |
| 7️⃣ | Ready for multi-agent orchestration and analytics |

---

## 🔐 API Key Setup

You’ll need an OpenAI API key.

1. Go to [https://platform.openai.com/account/api-keys](https://platform.openai.com/account/api-keys)
2. Click **Create new secret key** and copy it (`sk-...`)
3. Create a file `.env` in the project root with:

```
OPENAI_API_KEY=sk-YOUR-KEY-HERE
OPENAI_MODEL=gpt-4o
OPENAI_TEMPERATURE=0.2

# Optional toggles
SMALLTALK_FILTER=1
SMALLTALK_LLM_CLASSIFIER=0
SMALLTALK_CLASSIFIER_MODEL=gpt-4o-mini

# File paths
TRANSCRIPT_FILE=meeting_transcript.vtt
OUTPUT_DIR=output
OUTPUT_BASENAME=demo_run
```

---

## ⚙️ Architecture

```mermaid
flowchart TD
    A[Meeting Transcript (.vtt)] --> B[generate_req_bdd.py<br>Run Pipeline]
    B -->|OpenAI API| C[Requirements + BDD Test Cases]
    C --> D[(repo.db)]
    C --> E[output.json]
    D --> F[Flask UI app.py<br>Manual Approval]
    F --> D
    D --> G[export_csv.py]
    G --> H[CSV for Jira]
    B --> I[run_pipeline.py<br>E2E Controller]
```

---

## 🚀 How to Run

### 🧩 1. Install dependencies
```
pip install -r requirements.txt
```

### ▶️ 2. Run the full E2E pipeline
```
python run_pipeline.py
```

This will:
- Read your `meeting_transcript.vtt`
- Generate structured requirements + test cases
- Persist results in `output.json` and `repo.db`
- Export CSVs via `export_csv.py` (if present)
- Print a clean summary of results

You can also specify another transcript manually:
```
python generate_req_bdd.py path/to/another_transcript.vtt
```

---

## 🧱 Repository Structure

```
.
├── generate_req_bdd.py      # Core LLM pipeline (requirements + BDD)
├── run_pipeline.py          # E2E orchestrator (1-click flow)
├── export_csv.py            # CSV / Jira export
├── app.py                   # Optional Flask UI for approvals
├── meeting_transcript.vtt   # Example transcript input
├── output.json              # LLM output artifact
├── repo.db                  # SQLite persistence layer
├── requirements.txt         # Dependencies
└── output/                  # Demo CSV outputs
```

---

## 🧠 Recent Improvements (v2 – Agentic Ready)

### 🔄 Core Script (`generate_req_bdd.py`)
- Refactored into `run_pipeline()` callable for orchestration  
- Deterministic sequential IDs (`REQ-001`, `REQ-002`, …)  
- Exactly 3 acceptance criteria per requirement (pads/trims automatically)  
- Robust JSON parsing and fallback recovery  
- Normalized single-line Gherkin output  
- Graceful handling of empty transcripts  
- Same schema compatibility (`output.json`, `repo.db`)

### 🚀 New Orchestrator (`run_pipeline.py`)
- Single command to run the entire flow  
- Auto-calls `generate_req_bdd.py`  
- Optionally triggers `export_csv.py`  
- Outputs demo CSVs in `/output`  
- Prints clear metrics + file paths  
- Foundation for multi-agent control loop  

---

## 📊 Example Output

**Console summary**
```
📋 Extracted 5 requirements:
- REQ-001 Login authentication
- REQ-002 Password reset
...
✅ Generated 15 test cases (15 valid Gherkin)
🎯 Done. Lines kept: 210/248 (classifier=off)
🚀 E2E DONE
🧩 requirements: 5
✅ test cases:   15
📦 outputs:      output.json , repo.db
📑 csv:          output/demo_run_requirements.csv , output/demo_run_test_cases.csv
```

---

## 🧩 Next Steps
- Add **ReviewAgent** for validation and deduplication  
- Integrate **Jira API** for direct issue creation  
- Add **MetricsAgent** for baseline vs improved runs  
- Parallelize per-chunk LLM calls  
- Extend UI for “Approve → Export → Sync to Jira”

---

## 💡 Vision
> This POC shows how GenAI can move beyond text generation —  
> becoming an **autonomous system for requirements engineering**  
> that improves over time, validates itself, and connects directly to delivery tools.