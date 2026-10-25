import math

from fastapi.testclient import TestClient

from trading_gym.api.main import create_app


def bars():
    rows = []
    for i in range(90):
        price = 100 + math.sin(i / 5) * 5 + i * 0.03
        rows.append(
            {
                "at": 1704067200 + i * 300,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 10,
            }
        )
    return rows


def test_lab_uses_causal_replay_and_never_changes_account(tmp_path):
    with TestClient(
        create_app(tmp_path, start_connectors=False), base_url="http://127.0.0.1"
    ) as client:
        before = client.get("/api/v1/agent").json()
        response = client.post("/api/v1/agent/strategy-lab", json={"bars": bars()})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["report"]["execution_authority_granted"] is False
        assert data["report"]["venue_fills_verified"] is False
        assert len(data["report"]["input_sha256"]) == 64
        assert len(data["indicators"]) == 90
        assert client.get("/api/v1/agent").json()["trades"] == before["trades"]
        assert (
            client.post(
                "/api/v1/agent/strategy-lab", json={"bars": bars()[::-1]}
            ).status_code
            == 422
        )
        bad = bars()
        bad[0]["low"] = 1000
        assert (
            client.post("/api/v1/agent/strategy-lab", json={"bars": bad}).status_code
            == 422
        )
