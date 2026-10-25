import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from trading_gym.agent.gateway import ModelGateway
from trading_gym.agent.intelligence.llm_key_pool import CredentialsUnavailable
from trading_gym.core.local_secrets import LocalSecrets


def test_keys_are_encrypted_and_never_returned(tmp_path):
    vault = LocalSecrets(tmp_path)
    vault.update(
        abantether="test-broker-secret", llm=["test-model-one", "test-model-two"]
    )
    assert vault.key("abantether") == "test-broker-secret"
    assert len(vault.summary()["llm"]) == 2
    assert "test-model" not in json.dumps(vault.summary())
    assert "test-broker-secret" not in (tmp_path / "credentials.json").read_text()
    assert LocalSecrets(tmp_path).mapping("llm") == vault.mapping("llm")
    with pytest.raises(ValueError, match="Duplicate"):
        vault.update(llm=["duplicate", "duplicate"])


def test_gateway_rotates_keys_over_actual_http_and_honors_shared_limits(tmp_path):
    calls = []
    status = [200]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.headers.get("Authorization"))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(status[0])
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "5")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"choices": [{"message": {"content": '{"ok":true}'}}]}
                ).encode()
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        vault = LocalSecrets(tmp_path / "secrets")
        vault.update(llm=["first-test-key", "second-test-key"])
        gateway = ModelGateway(
            tmp_path / "gateway",
            vault,
            "llm",
            f"http://127.0.0.1:{server.server_port}/v1",
            "test",
        )
        assert json.loads(gateway.complete("Return JSON", {})) == {"ok": True}
        gateway.complete("Return JSON", {})
        assert calls == ["Bearer first-test-key", "Bearer second-test-key"]
        status[0] = 429
        with pytest.raises(CredentialsUnavailable):
            gateway.complete("Return JSON", {})
        before = len(calls)
        with pytest.raises(CredentialsUnavailable):
            gateway.complete("Return JSON", {})
        assert len(calls) == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
