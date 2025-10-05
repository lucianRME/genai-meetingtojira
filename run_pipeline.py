#!/usr/bin/env python3
"""
Runs your pipeline, then ensures CSVs and prints a clean summary.
"""
import os, sys, json, subprocess
from pathlib import Path

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def main():
    # 1) Run your generator in-process if available
    try:
        import generate_req_bdd as core
        print("▶ Running pipeline (in-process)…")
        result = core.run_pipeline(os.getenv("TRANSCRIPT_FILE"))
    except Exception as e:
        print(f"⚠️ In-process run failed or wrapper not applied yet: {e}")
        print("▶ Falling back to subprocess…")
        subprocess.run([sys.executable, "generate_req_bdd.py"], check=True)
        result = None

    # 2) Prefer your existing export script if present
    if Path("export_csv.py").exists():
        print("▶ Exporting CSVs via export_csv.py …")
        subprocess.run([sys.executable, "export_csv.py"], check=True)
        req_csv = Path("requirements.csv")
        tc_csv  = Path("test_cases.csv")
    else:
        # Minimal inline export from output.json (safe CSV via csv module recommended)
        print("ℹ️ export_csv.py not found — writing demo CSVs inline.")
        data = json.loads(Path("output.json").read_text(encoding="utf-8"))
        from csv import writer
        req_csv = OUTPUT_DIR / "demo_requirements.csv"
        with req_csv.open("w", encoding="utf-8", newline="") as f:
            w = writer(f)
            w.writerow(["id","title","description","acceptance_criteria"])
            for r in data.get("requirements", []):
                w.writerow([r.get("id",""), r.get("title",""), r.get("description",""),
                            " | ".join(r.get("acceptance_criteria", []))])
        tc_csv = OUTPUT_DIR / "demo_test_cases.csv"
        with tc_csv.open("w", encoding="utf-8", newline="") as f:
            w = writer(f)
            w.writerow(["requirement_id","scenario_type","gherkin"])
            for t in data.get("test_cases", []):
                w.writerow([t.get("requirement_id",""), t.get("scenario_type",""),
                            (t.get("gherkin","") or "").replace("\n"," ")])

    # 3) Print a crisp demo summary
    data = json.loads(Path("output.json").read_text(encoding="utf-8"))
    filt = data.get("filtering", {})
    print("\n🚀 E2E DONE")
    print(f"📄 lines: kept {filt.get('kept',0)}/{filt.get('total_lines',0)} (classifier={'on' if filt.get('use_llm_classifier') else 'off'})")
    print(f"🧩 requirements: {len(data.get('requirements', []))}")
    print(f"✅ test cases:    {len(data.get('test_cases', []))}")
    print(f"📦 outputs:       output.json , repo.db")
    print(f"📑 csv:           {req_csv} , {tc_csv}")

if __name__ == "__main__":
    main()