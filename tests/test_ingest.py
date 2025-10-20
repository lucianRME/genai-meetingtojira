# tests/test_ingest.py
from agents.ingest_agent import IngestAgent

def test_ingest_reads_and_filters(sample_vtt):
    ag = IngestAgent()
    out = ag.run({"transcript_path": sample_vtt})
    assert len(out["all_lines"]) >= 1
    # small talk line should be dropped by rule-based filter
    assert any("acceptance criteria" in l.lower() for l in out["filtered_lines"])
    assert any("good morning" in l.lower() for l in out["dropped_lines"])