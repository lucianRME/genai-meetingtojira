# tests/test_agents_pipeline.py
from agents.agentic_controller import Controller
from agents.ingest_agent import IngestAgent
from agents.requirements_agent import RequirementAgent
from agents.review_agent import ReviewAgent
from agents.tests_agent import TestAgent
from agents.persist_agent import PersistAgent

def test_agentic_flow_stubbed_llm(sample_vtt, db_conn, stub_chat):
    state = {"transcript_path": sample_vtt, "conn": db_conn, "db": db_conn,
             "project_id":"myproject", "session_id":"test-session"}
    flow = Controller([IngestAgent(), RequirementAgent(), ReviewAgent(), TestAgent(), PersistAgent()])
    res = flow.run(state)
    assert res.get("requirements")
    assert res.get("test_cases")