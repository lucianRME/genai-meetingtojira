import os, json, re, sqlite3
from dotenv import load_dotenv
from openai import OpenAI

# --- config ---
TRANSCRIPT_FILE = "meeting_transcript.vtt"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")  # sau gpt-4o-mini
TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))

# --- setup ---
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise SystemExit("Missing OPENAI_API_KEY. Put it in .env")
client = OpenAI(api_key=api_key)

def read_vtt_clean_text(path: str) -> str:
    text = open(path, "r", encoding="utf-8").read()
    text = re.sub(r"^WEBVTT\s*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}\s*\n", "", text)
    return text.strip()

def extract_json(s: str):
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE|re.DOTALL)
    return json.loads(s)

# -------- main --------
transcript_text = read_vtt_clean_text(TRANSCRIPT_FILE)

req_prompt = f"""
You are a business analyst. Extract 3-6 clear, testable business requirements from this meeting transcript.
Each requirement must have:
- id (format REQ-001, REQ-002, ...)
- title
- description
- acceptance_criteria: a list of exactly 3 short bullets

Transcript:
{transcript_text}

Return JSON array only, like:
[
  {{
    "id": "REQ-001",
    "title": "...",
    "description": "...",
    "acceptance_criteria": ["...", "...", "..."]
  }}
]
"""

resp1 = client.chat.completions.create(
    model=MODEL,
    messages=[{"role":"user","content":req_prompt}],
    temperature=TEMPERATURE
)
requirements = extract_json(resp1.choices[0].message.content)

bdd_prompt = f"""
You are a QA engineer. For each requirement below, generate 3 scenarios in Gherkin:
- one "positive"
- one "negative"
- one "regression"
Return JSON array only, with fields:
- requirement_id (e.g., "REQ-001")
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
test_cases = extract_json(resp2.choices[0].message.content)

with open("output.json","w",encoding="utf-8") as f:
    json.dump({"requirements":requirements, "test_cases":test_cases}, f, indent=2, ensure_ascii=False)

# save to SQLite
conn = sqlite3.connect("repo.db")
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS requirements (
  id TEXT PRIMARY KEY, title TEXT, description TEXT, criteria TEXT, approved INTEGER DEFAULT 0
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS test_cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requirement_id TEXT, scenario_type TEXT, gherkin TEXT
)""")

for r in requirements:
    cur.execute("INSERT OR REPLACE INTO requirements VALUES (?,?,?,?,0)",
        (r["id"], r["title"], r["description"], "\n".join(r["acceptance_criteria"])))
for t in test_cases:
    cur.execute("INSERT INTO test_cases (requirement_id,scenario_type,gherkin) VALUES (?,?,?)",
        (t["requirement_id"], t["scenario_type"], t["gherkin"]))
conn.commit(); conn.close()

print("✅ Generated requirements & test cases. See output.json and repo.db")