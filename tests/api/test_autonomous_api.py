from fastapi.testclient import TestClient
from trading_gym.api.main import create_app


def test_direct_access_and_cross_origin_rejection(tmp_path):
    with TestClient(
        create_app(tmp_path, start_connectors=False), base_url="http://127.0.0.1"
    ) as client:
        data = client.get("/api/v1/agent").json()
        assert data["mode"] == "observe"
        assert data["equity"] is None and data["cash"] is None
        assert data["trades"] == []
        assert (
            client.get(
                "/api/v1/agent", headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert (
            client.post("/api/v1/agent/start", json={"source": "example"}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/agent/start", json={"source": "broker", "mode": "live"}
            ).status_code
            == 409
        )


def test_api_keys_never_echo_in_responses_or_validation_errors(tmp_path):
    with TestClient(
        create_app(tmp_path, start_connectors=False), base_url="http://127.0.0.1"
    ) as client:
        secret = "unit-test-secret-value"
        response = client.put(
            "/api/v1/agent/credentials",
            json={"llm": [secret], "decision1": ["another-test-key"]},
        )
        assert response.status_code == 200
        assert secret not in response.text
        assert len(response.json()["llm"]) == 1
        for path in [
            "/api/v1/agent",
            "/api/v1/agent/settings",
            "/api/v1/agent/credentials",
        ]:
            assert secret not in client.get(path).text
        invalid = client.put("/api/v1/agent/credentials", json={"llm": [secret] * 17})
        assert invalid.status_code == 422 and secret not in invalid.text
        assert (
            client.put("/api/v1/agent/credentials", json={"llm": []}).json()["llm"]
            == []
        )


def test_history_has_real_empty_states_and_bounded_pagination(tmp_path):
    with TestClient(
        create_app(tmp_path, start_connectors=False), base_url="http://127.0.0.1"
    ) as client:
        assert client.get("/api/v1/agent/history").json() == {"total": 0, "items": []}
        assert client.get("/api/v1/agent/decisions").json() == {"total": 0, "items": []}
        assert client.get("/api/v1/agent/history?limit=10000").status_code == 422
        assert client.get("/api/v1/agent/decision?id=missing").status_code == 404
