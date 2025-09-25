import os, json, re, sqlite3
from dotenv import load_dotenv
from openai import OpenAI

# --- config / env ---
TRANSCRIPT_FILE = "meeting_transcript.vtt"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")           # or gpt-4o-mini
TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))

# Small-talk filtering toggles
SMALLTALK_FILTER = os.getenv("SMALLTALK_FILTER", "1") == "1"       # turn off with 0
USE_LLM_CLASSIFIER = os.getenv("SMALLTALK_LLM_CLASSIFIER", "0") == "1"  # turn on with 1
CLASSIFIER_MODEL = os.getenv("SMALLTALK_CLASSIFIER_MODEL", "gpt-4o-mini")

# --- setup ---
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise SystemExit("Missing OPENAI_API_KEY. Put it in .env")
client = OpenAI(api_key=api_key)

# --- helpers ---
def read_vtt_lines(path: str):
    """Return a list of speaker/text lines with timestamps removed."""
    text = open(path, "r", encoding="utf-8").read()
    # remove WEBVTT header
    text = re.sub(r"^WEBVTT\s*\n", "", text, flags=re.MULTILINE)
    # remove timecode lines
    text = re.sub(r"^\s*\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}\s*$", "", text, flags=re.MULTILINE)
    # remove empty cue numbers (optional)
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
    # normalize newlines
    text = re.sub(r"\r\n|\r", "\n", text)
    # split to lines, drop blanks
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return lines

SMALLTALK_KEYWORDS = [
    # greetings / chit-chat
    "good morning","good afternoon","good evening","hello everyone","hi everyone",
    "how are you","how’s everyone","weekend","coffee","weather","lunch","breakfast","dinner",
    "holiday","vacation","birthday","congrats","congratulations","nice to meet you",
    # filler / meeting admin
    "can you hear me","i'm on mute","you are on mute","let me share my screen",
    "next slide","previous slide","quick check","small talk",
    # sports / casual
    "the game last night","match last night","did you watch the game","netflix",
]

ACTION_HINTS = [
    # words that suggest business/action content
    "acceptance criteria","jira","story","epic","priority","owner","deadline","timeline",
    "bug","fix","release","sprint","backlog","mttr","sla","uat","qa","test","scenario",
    "deploy","environment","api","endpoint","rate limit","error","logging","monitoring",
    "security","authentication","authorization","mfa","otp","rollback","risk",
    "given","when","then","gherkin","requirements","spec","specification","design",
]

def rule_based_is_smalltalk(line: str) -> bool:
    """Cheap filter: flag as small talk if chit-chat keywords present AND no action hints."""
    l = line.lower()
    if any(kw in l for kw in SMALLTALK_KEYWORDS):
        # if it also contains action hints, keep it (meeting admin mixed with real content)
        if not any(h in l for h in ACTION_HINTS):
            return True
    # heuristics: extremely short lines with no punctuation often chit-chat
    if len(l) < 8 and l.isalpha():
        return True
    return False

def classify_line_llm(line: str) -> str:
    """
    Returns 'business' or 'small talk' using a lightweight model.
    Only used for ambiguous lines when USE_LLM_CLASSIFIER=1.
    """
    resp = client.chat.completions.create(
        model=CLASSIFIER_MODEL,
        messages=[
            {"role": "system", "content": "You classify meeting transcript lines as 'business' or 'small talk'. Reply with exactly one of: business | small talk."},
            {"role": "user", "content": f"Line: {line}"}
        ],
        temperature=0
    )
    label = resp.choices[0].message.content.strip().lower()
    return "business" if "business" in label else "small talk"

def filter_transcript_lines(lines):
    """
    1) Drop obvious small talk via rule-based filter
    2) (Optional) For ambiguous lines, call LLM classifier
    Returns filtered list of lines.
    """
    kept, dropped = [], []
    for ln in lines:
        # Rule-based quick decision
        rb_small = rule_based_is_smalltalk(ln)
        if rb_small and not USE_LLM_CLASSIFIER:
            dropped.append(ln); continue
        if rb_small and USE_LLM_CLASSIFIER:
            # Double check with LLM — only drop if LLM also says small talk
            label = classify_line_llm(ln)
            if label == "small talk":
                dropped.append(ln); continue
        kept.append(ln)
    return kept, dropped

def extract_json(s: str):
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE|re.DOTALL)
    return json.loads(s)

# -------- main --------
# 1) Read transcript as lines
all_lines = read_vtt_lines(TRANSCRIPT_FILE)

# 2) Optional small-talk filtering
if SMALLTALK_FILTER:
    filtered_lines, dropped_lines = filter_transcript_lines(all_lines)
else:
    filtered_lines, dropped_lines = all_lines, []

# 3) Build cleaned transcript for the LLM
transcript_text = "\n".join(filtered_lines).strip()

# 4) Requirements extraction
req_prompt = f"""
You are a business analyst. Extract 3-6 clear, testable business requirements from this meeting transcript.
Each requirement must have:
- id (format REQ-001, REQ-002, ...)
- title
- description
- acceptance_criteria: a list of exactly 3 short bullets

Transcript (noise-filtered):
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

# 5) BDD test generation
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

# 6) Persist outputs
with open("output.json","w",encoding="utf-8") as f:
    json.dump({
        "filtering": {
            "total_lines": len(all_lines),
            "kept": len(filtered_lines),
            "dropped": len(dropped_lines),
            "use_llm_classifier": USE_LLM_CLASSIFIER
        },
        "requirements": requirements,
        "test_cases": test_cases
    }, f, indent=2, ensure_ascii=False)

# 7) Save to SQLite
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

print(f"✅ Generated requirements & test cases. Lines kept: {len(filtered_lines)}/{len(all_lines)} "
      f"(LLM classifier={'on' if USE_LLM_CLASSIFIER else 'off'}). See output.json and repo.db")
