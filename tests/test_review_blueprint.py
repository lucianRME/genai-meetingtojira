# tests/test_review_blueprint.py
import app.review as rv
from flask import Flask

def test_review_index_renders(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(rv.bp)
    client = app.test_client()
    r = client.get("/review/")
    # first call ensures session cookie, table can be empty
    assert r.status_code == 200