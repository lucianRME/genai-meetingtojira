# tests/test_transcript_utils.py
from run_pipeline import _read_transcript_text, _quick_summarize

def test_read_vtt_and_summarize(sample_vtt):
    text = _read_transcript_text(sample_vtt)
    assert "Alice: Let's align." in text
    assert "Bob: OTP expires in 10 minutes." in text
    # summarizer clamps long text and preserves head/tail if needed
    s = _quick_summarize(text, max_len=40)
    assert len(s) <= 50