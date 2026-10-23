"""Exercise a built image's public UI, readiness and authenticated read only."""

import argparse
import json
from html.parser import HTMLParser
import secrets
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class _AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.stylesheets = []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "script" and fields.get("src"):
            self.scripts.append(fields["src"])
        if tag == "link" and fields.get("rel") == "stylesheet" and fields.get("href"):
            self.stylesheets.append(fields["href"])


def verify_installed_service(base: str, key: str) -> None:
    """Require built client assets and properly protected status reads."""
    with urlopen(base + "/", timeout=3) as response:
        if response.headers.get_content_type() != "text/html":
            raise RuntimeError("installed frontend did not serve HTML")
        html = response.read(1024 * 1024 + 1)
        if len(html) > 1024 * 1024 or b"<html" not in html.lower():
            raise RuntimeError("installed frontend HTML is absent or oversized")
    parser = _AssetParser()
    parser.feed(html.decode("utf-8"))
    if not parser.scripts:
        raise RuntimeError("installed frontend has no JavaScript entrypoint")
    assets = parser.scripts + parser.stylesheets
    if len(assets) > 32:
        raise RuntimeError("frontend asset inventory exceeds smoke budget")
    for asset in assets:
        if (
            not asset.startswith("/assets/")
            or ".." in asset.split("/")
            or "?" in asset
            or "#" in asset
            or "\\" in asset
        ):
            raise RuntimeError("frontend asset location is not a local build asset")
        with urlopen(base + asset, timeout=5) as response:
            mime = response.headers.get_content_type()
            expected = (
                {"text/javascript", "application/javascript"}
                if asset in parser.scripts
                else {"text/css"}
            )
            if mime not in expected:
                raise RuntimeError("frontend asset served with incorrect content type")
            contents = response.read(16 * 1024 * 1024 + 1)
            if not contents or len(contents) > 16 * 1024 * 1024:
                raise RuntimeError("frontend asset empty or oversized")

    for headers in ({}, {"X-API-Key": "intentionally-wrong-key"}):
        try:
            with urlopen(Request(base + "/api/v1/status", headers=headers), timeout=3):
                raise RuntimeError("protected status allowed unauthenticated access")
        except HTTPError as exc:
            if exc.code not in {401, 403}:
                raise RuntimeError("status rejected with unexpected response") from exc

    with urlopen(
        Request(base + "/api/v1/status", headers={"X-API-Key": key}), timeout=3
    ) as response:
        payload = json.loads(response.read(1024 * 1024 + 1))
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("processes"), dict)
            or not isinstance(payload.get("timestamp"), (int, float))
        ):
            raise RuntimeError("authenticated status schema is incomplete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    key = secrets.token_hex(32)
    name = "tradinggym-smoke-" + secrets.token_hex(6)
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--publish",
                "127.0.0.1::8000",
                "--env",
                "API_KEY=" + key,
                args.image,
            ],
            check=True,
            capture_output=True,
        )
        binding = subprocess.check_output(
            ["docker", "port", name, "8000/tcp"], text=True
        ).strip()
        base = "http://" + binding
        deadline = time.monotonic() + 90
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError("image readiness timeout")
            try:
                with urlopen(base + "/api/v1/ready", timeout=2) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError):
                pass
            time.sleep(0.5)
        verify_installed_service(base, key)
        print(
            "Installed image: UI, readiness and protected status passed; no trading mutation was requested."
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", name],
            check=False,
            capture_output=True,
        )


if __name__ == "__main__":
    main()
