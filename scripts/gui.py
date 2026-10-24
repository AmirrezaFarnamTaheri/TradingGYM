"""One local GUI bootstrap for Bash, PowerShell and direct Python callers.

Check/dry-run perform no writes, installs, configuration changes or launches.
Dependencies are installed with the existing Poetry/npm lockfiles. Secrets are
read as literal dotenv values; the file is never evaluated as shell code.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
HEALTH_PATH = "/api/v1/ready"


def port(value: str) -> int:
    result = int(value)
    if not 1 <= result <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return result


def prerequisites() -> None:
    if not (3, 11) <= sys.version_info[:2] < (3, 13):
        raise RuntimeError("Use Python 3.11 or 3.12, as declared by pyproject.toml")
    for name in ("poetry", "npm"):
        if not shutil.which(name):
            raise RuntimeError(f"Required tool unavailable: {name}; install it before setup")
    for relative in ("pyproject.toml", "poetry.lock", "frontend/package-lock.json"):
        if not (ROOT / relative).is_file():
            raise RuntimeError(f"Required project file missing: {relative}")


def environment() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.is_symlink():
        raise RuntimeError("Refusing a symbolic-link dotenv file")
    if path.exists():
        if path.stat().st_size > 64 * 1024:
            raise RuntimeError("Dotenv file exceeds 64 KiB")
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            name, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise RuntimeError(f"Invalid dotenv assignment on line {number}")
            value = value.strip()
            if value[:1] in {"'", '"'}:
                if len(value) < 2 or value[-1] != value[0]:
                    raise RuntimeError(f"Unclosed dotenv quote on line {number}")
                value = value[1:-1]
            if "\x00" in value:
                raise RuntimeError(f"Invalid dotenv value on line {number}")
            values[name] = value
    values.update(os.environ)  # An explicit caller environment takes precedence.
    values["POETRY_VIRTUALENVS_CREATE"] = "true"
    return values


def configure() -> None:
    path = ROOT / ".env"
    if path.is_symlink():
        raise RuntimeError("Refusing a symbolic-link dotenv file")
    if not path.exists():
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"# Gateway credential; never bundle this file\nAPI_KEY={secrets.token_hex(32)}\n")
        print("Created private .env. Use its API_KEY to open a browser session; it is not printed here.")


def wait_ready(process: subprocess.Popen, url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("API exited before readiness; inspect its startup output")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"API did not become ready at {url}; check configuration and startup output")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        process.wait(timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
        process.wait(timeout=5)


def start(api_port: int, frontend_port: int, skip_browser: bool) -> int:
    env = environment()
    if not env.get("API_KEY") or env["API_KEY"] in {
        "changeme-insecure-default-dev-only", "dev-insecure-key-change-me"
    }:
        raise RuntimeError("Configure API_KEY in .env or the caller environment before starting")
    env["TRADING_GYM_API_TARGET"] = f"http://127.0.0.1:{api_port}"
    env["VITE_API_URL"] = "/api/v1"
    env["VITE_WS_URL"] = f"ws://127.0.0.1:{api_port}"
    env.setdefault("ALLOWED_ORIGINS", f"http://localhost:{frontend_port},http://127.0.0.1:{frontend_port}")
    options = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    processes: list[subprocess.Popen] = []
    try:
        api = subprocess.Popen([shutil.which("poetry"), "run", "python", "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", str(api_port)], cwd=ROOT, env=env, **options)
        processes.append(api)
        wait_ready(api, f"http://127.0.0.1:{api_port}{HEALTH_PATH}")
        frontend = subprocess.Popen([shutil.which("npm"), "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"], cwd=ROOT / "frontend", env=env, **options)
        processes.append(frontend)
        url = f"http://127.0.0.1:{frontend_port}"
        print(f"Frontend starting at {url}; press Ctrl-C to stop both services.")
        if not skip_browser:
            webbrowser.open(url)
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        return next((process.returncode for process in processes if process.returncode is not None), 1)
    finally:
        for process in reversed(processes):
            stop(process)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=("setup", "start"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-start", action="store_true")
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--api-port", type=port, default=8000)
    parser.add_argument("--frontend-port", type=port, default=3000)
    args = parser.parse_args(argv)
    try:
        prerequisites()
        if args.check or args.dry_run:
            print(f"Prerequisites present; no state changed. {args.task}: Poetry environment, API {args.api_port}{HEALTH_PATH}, frontend {args.frontend_port}.")
            return 0
        if args.task == "setup":
            subprocess.run([sys.executable, str(ROOT / "scripts/project.py"), "setup"], cwd=ROOT, check=True)
            subprocess.run([shutil.which("npm"), "ci"], cwd=ROOT / "frontend", check=True)
            configure()
            if args.skip_start:
                return 0
        return start(args.api_port, args.frontend_port, args.skip_browser)
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"GUI bootstrap failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
