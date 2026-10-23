"""The installed smoke must verify served JS/CSS and reject unprotected APIs."""

import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError

SOURCE = Path(__file__).parents[2] / "scripts" / "smoke_container.py"
spec = importlib.util.spec_from_file_location("installed_smoke", SOURCE)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class Fixture(BaseHTTPRequestHandler):
    asset_available = True
    include_script = True
    auth_required = True

    def do_GET(self):
        if self.path == "/":
            script = (
                '<script type="module" src="/assets/app-123.js"></script>'
                if self.include_script
                else ""
            )
            body = (
                '<!doctype html><html><head><link rel="stylesheet" '
                'href="/assets/app-123.css"></head><body>'
                + script
                + "</body></html>"
            ).encode()
            status, content_type = 200, "text/html"
        elif self.path == "/assets/app-123.js" and self.asset_available:
            status, content_type, body = (
                200,
                "text/javascript",
                b'console.log("client")',
            )
        elif self.path == "/assets/app-123.css":
            status, content_type, body = 200, "text/css", b"body {display:block}"
        elif self.path == "/api/v1/status":
            supplied_key = self.headers.get("X-API-Key")
            if not self.auth_required or supplied_key == "super-secret":
                status, content_type, body = (
                    200,
                    "application/json",
                    b'{"processes":{},"timestamp":0}',
                )
            else:
                status, content_type, body = (
                    403 if supplied_key else 401,
                    "application/json",
                    b'{"detail":"Unauthorized"}',
                )
        else:
            status, content_type, body = 404, "text/plain", b"not found"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class InstalledSmokeTests(unittest.TestCase):
    def setUp(self):
        Fixture.asset_available = True
        Fixture.include_script = True
        Fixture.auth_required = True
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def test_serves_client_assets_and_protected_status(self):
        smoke.verify_installed_service(self.base, "super-secret")

    def test_missing_javascript_is_not_a_successful_container(self):
        Fixture.asset_available = False
        with self.assertRaises(HTTPError):
            smoke.verify_installed_service(self.base, "super-secret")

    def test_html_shell_without_js_is_not_a_working_client(self):
        Fixture.include_script = False
        with self.assertRaisesRegex(RuntimeError, "no JavaScript entrypoint"):
            smoke.verify_installed_service(self.base, "super-secret")

    def test_status_must_reject_unauthenticated_access(self):
        Fixture.auth_required = False
        with self.assertRaisesRegex(RuntimeError, "unauthenticated"):
            smoke.verify_installed_service(self.base, "super-secret")


if __name__ == "__main__":
    unittest.main()
