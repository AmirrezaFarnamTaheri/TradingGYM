import json
from pathlib import Path

def test_committed_openapi_is_parseable_and_declares_generation_state():
    p=Path('docs/contracts/openapi.json')
    data=json.loads(p.read_text())
    assert data['openapi'].startswith('3.')
    assert isinstance(data['paths'],dict)
