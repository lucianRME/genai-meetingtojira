#!/usr/bin/env python3
"""
generate_req_bdd.py

Reads a meeting transcript (VTT), filters small talk, extracts structured
requirements, generates BDD test cases, and persists outputs to JSON + SQLite.

- Compatible with your existing repo artifacts: output.json, repo.db
- Callable via run_pipeline(transcript_path=None) OR as a script
- Ensures requirement IDs are sequential (REQ-001, ...) and each has exactly 3 AC items
"""

from __future__ import annotations
import os, sys, re, json, sqlite3
from typing import List, Tuple
from dotenv import load_dotenv
from openai import OpenAI

# ----------------------------
# Setup & Configuration
# ----------------------------
load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise SystemExit("Missing OPENAI_API_KEY. Put it in .env")

# OpenAI client
client = OpenAI(api_key=api_key)

# Model config
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))

# Small-talk filtering
SMALLTALK_FILTER = os.getenv("SMALLTALK_FILTER", "1") == "1"
SMALLTALK_LLM_CLASSIFIER = os.getenv("SMALLTALK_LLM_CLASSIFIER", "0") == "1"
CLASSIFIER_MODEL = os.getenv("SMALLTALK_CLASSIFIER_MODEL", "gpt-4o-mini")

# Files (env overridable)
TRANSCRIPT_FILE = os.getenv("TRANSCRIPT_FILE", "meeting_transcript.vtt")

# ----------------------------
# Helpers: I/O & Filtering
# ----------------------------
def read_vtt_lines(path: str) -> List[str]:
    """Return transcript lines with timecodes/headers removed."""
    text = open(path, "r", encoding="utf-8").read()
    text = re.sub(r"^WEBVTT\s*\n", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"^\s*\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}\s*$",
        "", text, flags=re.MULTILINE
    )
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)  # cue numbers
    text = re.sub(r"\r\n|\r", "\n", text)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return lines

SMALLTALK_KEYWORDS = [
    # greetings / chit-chat
    "good morning","good afternoon","good evening","hello everyone","hi everyone",
    "how are you","how’s everyone","weekend","coffee","weather","lunch","breakfast",
    "dinner","holiday","vacation","birthday","congrats","congratulations",
    "nice to meet you",
    # meeting admin
    "can you hear me","i'm on mute","you are on mute","let me share my screen",
    "next slide","previous slide","quick check","small talk",
    # casual
    "the game last night","did you watch the game","netflix",
]

ACTION_HINTS = [
    "acceptance criteria","jira","story","epic","priority","owner","deadline","timeline",
    "bug","fix","release","sprint","backlog","mttr","sla","uat","qa","test","scenario",
    "deploy","environment","api","endpoint","rate limit","error","logging","monitoring",
    "security","authentication","authorization","mfa","otp","rollback","risk",
    "given","when","then","gherkin","requirement","spec","specification","design",
]

def rule_based_is_smalltalk(line: str) -> bool:
    """
    Conservative filter: flag as small talk if chit-chat keywords present
    AND no action hints; also drop very short purely alpha tokens.
    """
    l = line.lower()
    if any(kw in l for kw in SMALLTALK_KEYWORDS) and not any(h in l for h in ACTION_HINTS):
        return True
    if len(l) < 8 and l.isalpha():
        return True
    return False

def classify_line_llm(line: str) -> str:
    """
    Returns 'business' or 'small talk' using a lightweight model (if enabled).
    """
    resp = client.chat.completions.create(
        model=CLASSIFIER_MODEL,
        messages=[
            {"role": "system", "content": "Classify meeting transcript lines. Reply exactly: business OR small talk."},
            {"role": "user", "content": line}
        ],
        temperature=0
    )
    label = (resp.choices[0].message.content or "").strip().lower()
    return "business" if "business" in label else "small talk"

def filter_transcript_lines(lines: List[str]) -> Tuple[List[str], List[str]]:
    """
    1) Drop obvious small talk by rules
    2) If enabled, disambiguate via LLM
    """
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

# ----------------------------
# Helpers: JSON & Validation
# ----------------------------
def extract_json_forgiving(s: str):
    """
    Robust JSON extraction from model output that may include prose or code fences.
    """
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Fallback: find first balanced JSON object/array
    starts = [i for i, ch in enumerate(s) if ch in "[{"]
    for start in starts:
        stack = []
        for i in range(start, len(s)):
            ch = s[i]
            if ch in "[{":
                stack.append(ch)
            elif ch in "]}":
                if not stack:
                    break
                opener = stack.pop()
                if (opener, ch) not in {("[", "]"), ("{", "}")}:
                    break
                if not stack:
                    candidate = s[start:i+1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        pass
    raise ValueError("Could not extract valid JSON from model output.")

def validate_gherkin(text: str) -> bool:
    """Minimal Gherkin sanity check."""
    if not text:
        return False
    return all(token in text for token in ["Scenario:", "Given", "When", "Then"])

def normalize_gherkin(text: str) -> str:
    """Collapse excessive whitespace while keeping a single line."""
    return re.sub(r"[ \t]+", " ", (text or "").replace("\n", " ")).strip()

def enforce_ids_and_ac(requirements: List[dict]) -> List[dict]:
    """
    Ensure IDs are unique and sequential (REQ-001, REQ-002, ...),
    and acceptance_criteria has exactly 3 short bullets.
    """
    fixed = []
    for i, r in enumerate(requirements, 1):
        rid = f"REQ-{i:03d}"
        ac = r.get("acceptance_criteria", []) or []
        ac = [a.strip() for a in ac if a and str(a).strip()]
        if len(ac) < 3:
            ac = ac + ["TBD"] * (3 - len(ac))
        elif len(ac) > 3:
            ac = ac[:3]
        r2 = {
            **r,
            "id": rid,
            "acceptance_criteria": ac
        }
        fixed.append(r2)
    return fixed

# ----------------------------
# Core Pipeline
# ----------------------------
def run_pipeline(transcript_path: str | None = None):
    path = transcript_path or TRANSCRIPT_FILE

    # 1) Read transcript
    all_lines = read_vtt_lines(path)

    # 2) Optional small-talk filtering
    if SMALLTALK_FILTER:
        filtered_lines, dropped_lines = filter_transcript_lines(all_lines)
    else:
        filtered_lines, dropped_lines = all_lines, []

    if not filtered_lines:
        print("No content after filtering. Exiting.")
        # Still write minimal artifacts for consistency
        with open("output.json","w",encoding="utf-8") as f:
            json.dump({
                "filtering": {
                    "total_lines": len(all_lines),
                    "kept": 0,
                    "dropped": len(dropped_lines),
                    "use_llm_classifier": SMALLTALK_LLM_CLASSIFIER
                },
                "requirements": [],
                "test_cases": []
            }, f, indent=2, ensure_ascii=False)
        # Ensure DB exists with tables
        conn = sqlite3.connect("repo.db"); cur = conn.cursor()
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
          gherkin TEXT,
          tags TEXT
        )""")
        conn.commit(); conn.close()
        return {
            "output_json": "output.json",
            "db_path": "repo.db",
            "requirements_count": 0,
            "test_cases_count": 0,
            "filtering": {"total": len(all_lines), "kept": 0}
        }

    transcript_text = "\n".join(filtered_lines).strip()

    # 3) Requirements extraction
    req_prompt = f"""
You are a senior business analyst. Extract 3–6 clear, testable business requirements from this transcript.
Each requirement must have:
- id (REQ-001, REQ-002, ...)
- title
- description
- acceptance_criteria: exactly 3 short bullets
- priority (High/Medium/Low, based on context)
- epic (string, or null if not detectable)

Transcript (noise-filtered):
{transcript_text}

Return JSON array only.
""".strip()

    resp1 = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": req_prompt}],
        temperature=TEMPERATURE
    )
    raw_req = resp1.choices[0].message.content

    try:
        requirements = extract_json_forgiving(raw_req)
    except Exception as e:
        print("⚠️ Could not parse requirements JSON:", e)
        with open("llm_raw_req.txt", "w", encoding="utf-8") as f:
            f.write(raw_req or "")
        raise

    # Stabilize IDs & AC length
    requirements = enforce_ids_and_ac(requirements)

    print(f"📋 Extracted {len(requirements)} requirements:")
    for r in requirements:
        print("-", r.get("id"), r.get("title"))

    # 4) BDD test generation
    bdd_prompt = f"""
You are a QA engineer. For each requirement below, generate 3 scenarios in Gherkin:
- one "positive"
- one "negative"
- one "regression"

Rules:
- gherkin must start with 'Scenario:'
- each scenario must include at least one Given, one When, and one Then
- short, simple sentences
- avoid referencing undefined requirements
- include tags: @positive, @negative, @regression

Return JSON array only, with:
- requirement_id
- scenario_type ("positive"/"negative"/"regression")
- gherkin (single string; include 'Scenario:' and Given/When/Then)
- tags (array, e.g. ["@positive"])

Requirements:
{json.dumps(requirements, ensure_ascii=False, indent=2)}
""".strip()

    resp2 = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": bdd_prompt}],
        temperature=TEMPERATURE
    )
    raw_tests = resp2.choices[0].message.content

    try:
        test_cases = extract_json_forgiving(raw_tests)
    except Exception as e:
        print("⚠️ Could not parse test cases JSON:", e)
        with open("llm_raw_tests.txt", "w", encoding="utf-8") as f:
            f.write(raw_tests or "")
        raise

    # Normalize & minimally validate Gherkin
    for t in test_cases:
        t["gherkin"] = normalize_gherkin(t.get("gherkin", ""))
    valid_count = sum(1 for t in test_cases if validate_gherkin(t.get("gherkin", "")))
    print(f"✅ Generated {len(test_cases)} test cases ({valid_count} valid Gherkin)")

    # 5) Persist: JSON artifact
    with open("output.json","w",encoding="utf-8") as f:
        json.dump({
            "filtering": {
                "total_lines": len(all_lines),
                "kept": len(filtered_lines),
                "dropped": len(dropped_lines),
                "use_llm_classifier": SMALLTALK_LLM_CLASSIFIER
            },
            "requirements": requirements,
            "test_cases": test_cases
        }, f, indent=2, ensure_ascii=False)

    # 6) Persist: SQLite (with metadata + tags)
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
      gherkin TEXT,
      tags TEXT
    )""")

    for r in requirements:
        cur.execute(
            "INSERT OR REPLACE INTO requirements (id,title,description,criteria,priority,epic,approved) "
            "VALUES (?,?,?,?,?,?,COALESCE((SELECT approved FROM requirements WHERE id=?),0))",
            (
                r["id"], r.get("title",""), r.get("description",""),
                "\n".join(r.get("acceptance_criteria", [])),
                r.get("priority"), r.get("epic"),
                r["id"]
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

    print(
        f"🎯 Done. Lines kept: {len(filtered_lines)}/{len(all_lines)} "
        f"(classifier={'on' if SMALLTALK_LLM_CLASSIFIER else 'off'}). "
        "See output.json and repo.db"
    )

    return {
        "output_json": "output.json",
        "db_path": "repo.db",
        "requirements_count": len(requirements),
        "test_cases_count": len(test_cases),
        "filtering": {"total": len(all_lines), "kept": len(filtered_lines)}
    }

# ----------------------------
# Script entry point
# ----------------------------
if __name__ == "__main__":
    # Optional CLI override: python generate_req_bdd.py [path/to/transcript.vtt]
    transcript = sys.argv[1] if len(sys.argv) > 1 else None
    run_pipeline(transcript)