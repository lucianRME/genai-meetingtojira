import os, json, re, sqlite3
from dotenv import load_dotenv
from openai import OpenAI

# --- config / env ---
TRANSCRIPT_FILE = "meeting_transcript.vtt"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))

# Small-talk filtering
SMALLTALK_FILTER = os.getenv("SMALLTALK_FILTER", "1") == "1"
SMALLTALK_LLM_CLASSIFIER = os.getenv("SMALLTALK_LLM_CLASSIFIER", "0") == "1"
CLASSIFIER_MODEL = os.getenv("SMALLTALK_CLASSIFIER_MODEL", "gpt-4o-mini")

# --- setup ---
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise SystemExit("Missing OPENAI_API_KEY. Put it in .env")
client = OpenAI(api_key=api_key)

# --- helpers ---
def read_vtt_lines(path: str):
    """Return transcript lines with timestamps removed."""
    text = open(path, "r", encoding="utf-8").read()
    text = re.sub(r"^WEBVTT\s*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return lines

SMALLTALK_KEYWORDS = ["good morning","weekend","coffee","weather","birthday","vacation","lunch","mute","share screen"]
ACTION_HINTS = ["requirement","acceptance criteria","jira","test","api","bug","sprint","deploy","given","when","then"]

def rule_based_is_smalltalk(line: str) -> bool:
    l = line.lower()
    if any(kw in l for kw in SMALLTALK_KEYWORDS) and not any(h in l for h in ACTION_HINTS):
        return True
    return False

def classify_line_llm(line: str) -> str:
    resp = client.chat.completions.create(
        model=CLASSIFIER_MODEL,
        messages=[
            {"role": "system", "content": "Classify transcript lines as 'business' or 'small talk'."},
            {"role": "user", "content": line}
        ],
        temperature=0
    )
    label = resp.choices[0].message.content.strip().lower()
    return "business" if "business" in label else "small talk"

def filter_transcript_lines(lines):
    kept, dropped = [], []
    for ln in lines:
        if rule_based_is_smalltalk(ln):
            if SMALLTALK_LLM_CLASSIFIER:
                label = classify_line_llm(ln)
                if label == "small talk":
                    dropped.append(ln); continue
            else:
                dropped.append(ln); continue
        kept.append(ln)
    return kept, dropped

def extract_json(s: str):
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE|re.DOTALL)
    return json.loads(s)

# -------- main --------
# 1) Read transcript
all_lines = read_vtt_lines(TRANSCRIPT_FILE)
if SMALLTALK_FILTER:
    filtered_lines, dropped_lines = filter_transcript_lines(all_lines)
else:
    filtered_lines, dropped_lines = all_lines, []
transcript_text = "\n".join(filtered_lines)

# 2) Prompt for requirements
req_prompt = f"""
You are a business analyst. Extract 3–6 clear, testable business requirements from this transcript.
Each requirement must have:
- id (REQ-001, REQ-002, ...)
- title
- description
- acceptance_criteria: exactly 3 short bullets
- priority (High/Medium/Low, based on context)
- epic (string, or null if not detectable)

Transcript:
{transcript_text}

Return JSON array only.
"""

resp1 = client.chat.completions.create(
    model=MODEL,
    messages=[{"role":"user","content":req_prompt}],
    temperature=TEMPERATURE
)

raw_req = resp1.choices[0].message.content
try:
    requirements = extract_json(raw_req)
except Exception as e:
    print("⚠️ Could not parse requirements JSON:", e)
    with open("llm_raw_req.txt", "w", encoding="utf-8") as f:
        f.write(raw_req)
    raise

print(f"📋 Extracted {len(requirements)} requirements:")
for r in requirements:
    print("-", r["id"], r["title"])

# 3) Prompt for BDD test cases
bdd_prompt = f"""
You are a QA engineer. For each requirement below, generate 3 scenarios in Gherkin:
- one "positive"
- one "negative"
- one "regression"
Return JSON array only, with:
- requirement_id
- scenario_type ("positive"/"negative"/"regression")
- gherkin (single string; include 'Scenario:' and Given/When/Then)

Requirements:
{json.dumps(requirements, ensure_ascii=False, indent=2)}
"""

resp2 = client.chat.completions.create(
    model=MODEL,
    messages=[{"role":"user","content":bdd_prompt}],
    temperature=TEMPERATURE
)

raw_tests = resp2.choices[0].message.content
try:
    test_cases = extract_json(raw_tests)
except Exception as e:
    print("⚠️ Could not parse test cases JSON:", e)
    with open("llm_raw_tests.txt", "w", encoding="utf-8") as f:
        f.write(raw_tests)
    raise

# 4) Save outputs
with open("output.json","w",encoding="utf-8") as f:
    json.dump({
        "filtering": {"total_lines": len(all_lines),
                      "kept": len(filtered_lines),
                      "dropped": len(dropped_lines),
                      "use_llm_classifier": SMALLTALK_LLM_CLASSIFIER},
        "requirements": requirements,
        "test_cases": test_cases
    }, f, indent=2, ensure_ascii=False)

# 5) Save to SQLite
conn = sqlite3.connect("repo.db")
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS requirements (
  id TEXT PRIMARY KEY,
  title TEXT,
  description TEXT,
  criteria TEXT,
  priority TEXT,
  epic TEXT,
  approved INTEGER DEFAULT 0
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS test_cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requirement_id TEXT,
  scenario_type TEXT,
  gherkin TEXT
)""")

for r in requirements:
    cur.execute("INSERT OR REPLACE INTO requirements VALUES (?,?,?,?,?,?,0)",
        (r["id"], r["title"], r["description"],
         "\n".join(r["acceptance_criteria"]),
         r.get("priority"), r.get("epic")))
for t in test_cases:
    cur.execute("INSERT INTO test_cases (requirement_id,scenario_type,gherkin) VALUES (?,?,?)",
        (t["requirement_id"], t["scenario_type"], t["gherkin"]))
conn.commit(); conn.close()

print("✅ Requirements & test cases saved. See output.json and repo.db")
