"""POST /learning/reflect API test: the manual trigger for the same
reflection cycle ReflectionScheduler runs on a clock."""

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from omni.api.main import app

client = TestClient(app)


def test_learning_reflect_endpoint_proposes_and_lands_in_the_ledger():
    proposal = json.dumps(
        {
            "has_proposal": True,
            "domain": "agent",
            "title": "Split the Support Agent by channel",
            "description": "Support Agent queue depth is consistently the highest in the fleet.",
            "hypothesis": "Splitting by channel reduces average wait time.",
        }
    )
    with patch("omni.agents.reflection.call_llm", return_value=proposal):
        r = client.post("/learning/reflect")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "proposed"
    assert body["proposal_id"] is not None

    ledger = client.get("/evolution/ledger").json()
    assert any(p["id"] == body["proposal_id"] for p in ledger)
