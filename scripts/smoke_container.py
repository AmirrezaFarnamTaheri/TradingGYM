"""Exercise a built image's public UI, readiness and authenticated read only."""
import argparse
import json
import secrets
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image')
    args = parser.parse_args()
    key = secrets.token_hex(32)
    name = 'tradinggym-smoke-' + secrets.token_hex(6)
    try:
        subprocess.run(['docker', 'run', '--detach', '--name', name, '--publish', '127.0.0.1::8000', '--env', 'API_KEY=' + key, args.image], check=True, capture_output=True)
        binding = subprocess.check_output(['docker', 'port', name, '8000/tcp'], text=True).strip()
        base = 'http://' + binding
        deadline = time.monotonic() + 90
        while True:
            try:
                with urlopen(base + '/api/v1/ready', timeout=2) as response:
                    if response.status == 200: break
            except (URLError, TimeoutError):
                if time.monotonic() > deadline: raise RuntimeError('image readiness timeout')
                time.sleep(0.5)
        with urlopen(base + '/', timeout=3) as response:
            assert b'<html' in response.read(1024 * 1024).lower()
        with urlopen(Request(base + '/api/v1/status', headers={'X-API-Key': key}), timeout=3) as response:
            assert isinstance(json.load(response), dict)
        print('Installed image: UI, readiness and protected status passed; no trading mutation was requested.')
    finally:
        subprocess.run(['docker', 'rm', '--force', '--volumes', name], check=False, capture_output=True)


if __name__ == '__main__': main()
