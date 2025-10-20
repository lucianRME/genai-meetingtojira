from run_pipeline import _read_transcript_text, _quick_summarize

def test_read_vtt_and_summarize(sample_vtt):
    text = _read_transcript_text(sample_vtt)
    assert "acceptance criteria for checkout" in text.lower()

    long = " ".join(["abc"] * 200)
    s = _quick_summarize(long, max_len=50)
    assert len(s) <= 50
    assert "…" in s or "..." in s
