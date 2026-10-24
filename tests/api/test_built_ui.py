from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.api.ui import mount_built_ui


def test_installed_ui_preserves_api_routes(tmp_path):
    app = FastAPI()
    @app.get('/api/v1/health')
    def health(): return {'status': 'healthy'}
    (tmp_path / 'index.html').write_text('<main>fixture UI</main>')
    assert mount_built_ui(app, tmp_path)
    with TestClient(app) as client:
        assert client.get('/').text == '<main>fixture UI</main>'
        assert client.get('/api/v1/health').json() == {'status': 'healthy'}
        assert client.get('/../pyproject.toml').status_code == 404


def test_missing_build_is_explicit(tmp_path):
    app = FastAPI()
    assert not mount_built_ui(app, tmp_path)
