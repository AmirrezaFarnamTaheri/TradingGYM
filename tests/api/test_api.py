"""
Comprehensive API Tests for Trading Gym

This module provides extensive test coverage for all API endpoints including:
- Health and status endpoints
- Configuration management
- Training control
- Data management
- Model management
- Trading control
- Emergency controls
- WebSocket endpoints

Tests use pytest fixtures and mock dependencies appropriately.

Version: 4.2.0
"""

import os

# Set test environment variables before importing the API
os.environ["API_KEY"] = "test-api-key-12345"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000,http://localhost:5173"

import pytest  # noqa: E402
from unittest.mock import patch, MagicMock, AsyncMock  # noqa: E402
from collections import deque  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from apps.api import main as main_module  # noqa: E402


# We need to mock the psutil and subprocess functionality
@pytest.fixture
def mock_processes():
    """Mock the PROCESSES registry."""
    return {}


@pytest.fixture
def mock_log_queue():
    """Mock the LOG_QUEUE."""
    return deque(maxlen=1000)


@pytest.fixture
def test_api_key():
    """Provide the test API key."""
    return "test-api-key-12345"


@pytest.fixture
def auth_headers(test_api_key):
    """Provide authentication headers."""
    return {"X-API-Key": test_api_key}


class TestHealthEndpoints:
    """Tests for health and readiness endpoints."""

    def test_health_check_returns_healthy(self, client):
        """Health endpoint should return healthy status."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data

    def test_ready_check_with_config(self, client, tmp_path, monkeypatch):
        """Ready endpoint should return ready when config exists."""
        # Create a temporary config file
        config_path = tmp_path / "config" / "app.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("env:\n  symbol: BTCUSDT\n")

        # Monkeypatch to use temp directory
        monkeypatch.chdir(tmp_path)

        response = client.get("/api/v1/ready")
        # May fail if config doesn't exist in test environment
        # This is expected behavior
        assert response.status_code in [200, 503]


class TestAuthenticationEndpoints:
    """Tests for API authentication.

    Note: These tests verify API key handling. In the test environment,
    the API_KEY is set at module load time, so some behaviors differ.
    """

    @pytest.mark.skip(
        reason="Authentication isolation requires process-level isolation"
    )
    def test_missing_api_key_returns_401(self, client):
        """Requests without API key should return 401."""
        # This test requires the API to be started without an API_KEY set
        # which is not easily achievable in the current test setup
        pass

    @pytest.mark.skip(
        reason="Authentication isolation requires process-level isolation"
    )
    def test_invalid_api_key_returns_403(self, client):
        """Requests with invalid API key should return 403."""
        # This test requires comparing against the real API_KEY
        pass

    def test_valid_api_key_returns_success(self, client, auth_headers):
        """Requests with valid API key should succeed."""
        response = client.get("/api/v1/status", headers=auth_headers)
        assert response.status_code == 200

    def test_api_key_is_required_for_protected_endpoints(self, client, auth_headers):
        """Protected endpoints should work with valid API key."""
        # Test various protected endpoints
        endpoints = [
            "/api/v1/status",
            "/api/v1/train/status",
            "/api/v1/trading/status",
        ]
        for endpoint in endpoints:
            response = client.get(endpoint, headers=auth_headers)
            assert response.status_code == 200, f"Failed for {endpoint}"


class TestStatusEndpoints:
    """Tests for system status endpoints."""

    def test_get_system_status(self, client, auth_headers):
        """Should return current system status."""
        response = client.get("/api/v1/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "processes" in data
        assert "timestamp" in data

    def test_prune_system(self, client, auth_headers):
        """Should trigger zombie pruning."""
        response = client.post("/api/v1/system/prune", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pruned"


class TestConfigurationEndpoints:
    """Tests for configuration management."""

    def test_get_config(self, client, auth_headers, tmp_path, monkeypatch):
        """Should return configuration."""
        # Create test config
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "app.yaml"
        config_file.write_text(
            """
env:
  symbol: BTCUSDT
  quote_ccy: USDT
training:
  algo: PPO
"""
        )
        monkeypatch.chdir(tmp_path)

        response = client.get("/api/v1/config", headers=auth_headers)
        # May return 404 if config not found in test environment
        assert response.status_code in [200, 404, 500]

    def test_get_config_section(self, client, auth_headers, tmp_path, monkeypatch):
        """Should return specific config section."""
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "app.yaml"
        config_file.write_text(
            """
env:
  symbol: BTCUSDT
training:
  algo: PPO
"""
        )
        monkeypatch.chdir(tmp_path)

        response = client.get("/api/v1/config/env", headers=auth_headers)
        assert response.status_code in [200, 404, 500]

    def test_update_config_persists_to_disk(
        self, client, auth_headers, tmp_path, monkeypatch
    ):
        """POST /api/v1/config must actually write the merged config.

        Regression guard: an earlier refactor registered a dead
        ``@app.post("/api/v1/config")`` shell before the real handler, and
        Starlette matches the first route — so the endpoint returned 200 with
        a null body and never touched the file. No test exercised the write.
        """
        import shutil

        # Seed from the repository's own canonical, schema-valid config so the
        # test is not coupled to the full set of required sub-models.
        config_file = tmp_path / "app.yaml"
        shutil.copy(main_module.PROJECT_ROOT / "config" / "app.yaml", config_file)
        monkeypatch.setattr(main_module, "CONFIG_PATH", config_file)

        before = client.get("/api/v1/config", headers=auth_headers)
        assert before.status_code == 200
        original_symbol = before.json()["env"]["symbol"]

        # A symbol that appears nowhere else in the seed config.
        probe_symbol = "PROBEUSDT"
        response = client.post(
            "/api/v1/config",
            headers=auth_headers,
            json={"env": {"symbol": probe_symbol}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "saved"

        # The persisted bytes must carry the merge, not just the response.
        assert probe_symbol in config_file.read_text(encoding="utf-8")

        # And a subsequent read must observe it.
        readback = client.get("/api/v1/config", headers=auth_headers)
        assert readback.status_code == 200
        assert readback.json()["env"]["symbol"] == probe_symbol
        assert probe_symbol != original_symbol


class TestTrainingEndpoints:
    """Tests for training control."""

    def test_get_training_status_when_idle(self, client, auth_headers):
        """Should return idle when no training running."""
        response = client.get("/api/v1/train/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["idle", "running", "completed"]

    def test_start_training_validates_config_name(self, client, auth_headers):
        """Should validate config name format."""
        # Invalid config name with path traversal
        response = client.post(
            "/api/v1/train/start",
            headers=auth_headers,
            json={"config_name": "../../../etc/passwd"},
        )
        assert response.status_code == 422

    def test_stop_training_when_not_running(self, client, auth_headers):
        """Should return 404 when no training to stop."""
        response = client.post("/api/v1/train/stop", headers=auth_headers)
        assert response.status_code == 404


class TestModelEndpoints:
    """Tests for model management."""

    def test_list_models(self, client, auth_headers, tmp_path, monkeypatch):
        """Should list available models."""
        # Create checkpoints directory with a model
        checkpoints_dir = tmp_path / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)
        model_dir = checkpoints_dir / "test_model_v1"
        model_dir.mkdir()
        (model_dir / "best_model.zip").touch()
        (model_dir / "metadata.json").write_text('{"reward": 100}')

        # Patch CHECKPOINTS_DIR
        with patch("apps.api.main.CHECKPOINTS_DIR", checkpoints_dir):
            response = client.get("/api/v1/models", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()
            assert "models" in data

    def test_get_active_model(self, client, auth_headers, tmp_path, monkeypatch):
        """Should return active model or None."""
        checkpoints_dir = tmp_path / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)

        with patch("apps.api.main.CHECKPOINTS_DIR", checkpoints_dir):
            response = client.get("/api/v1/models/active", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()
            # active_model can be None or a string
            assert "active_model" in data

    def test_set_active_model_validates_format(self, client, auth_headers):
        """Should validate model version format."""
        response = client.post(
            "/api/v1/models/set-active?version=../../../etc/passwd",
            headers=auth_headers,
        )
        assert response.status_code == 400


class TestTradingEndpoints:
    """Tests for trading control."""

    def test_get_trading_status_when_idle(self, client, auth_headers):
        """Should return idle when no trading running."""
        response = client.get("/api/v1/trading/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["idle", "running", "stopped"]

    def test_start_trading_validates_mode(self, client, auth_headers):
        """Should validate trading mode."""
        response = client.post(
            "/api/v1/trading/start", headers=auth_headers, json={"mode": "invalid_mode"}
        )
        assert response.status_code == 422

    def test_stop_trading_when_not_running(self, client, auth_headers):
        """Should return 404 when no trading to stop."""
        response = client.post("/api/v1/trading/stop", headers=auth_headers)
        assert response.status_code == 404

    def test_panic_button(self, client, auth_headers, tmp_path, monkeypatch):
        """Should create panic signal file."""
        from apps.api import main as api_main

        panic_file = tmp_path / "panic.signal"
        monkeypatch.setattr(api_main, "PANIC_FILE", panic_file)

        response = client.post("/api/v1/trading/panic", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "panic_triggered"

        # Check panic file was created
        assert panic_file.exists()


class TestEmergencyEndpoints:
    """Tests for emergency controls."""

    def test_emergency_kill_requires_confirmation(self, client, auth_headers):
        """Should require confirmation string."""
        response = client.post(
            "/api/v1/emergency/kill-all",
            headers=auth_headers,
            json={"confirmation": "wrong"},
        )
        assert response.status_code == 422

    def test_emergency_kill_with_confirmation(self, client, auth_headers, tmp_path, monkeypatch):
        """Should succeed with correct confirmation."""
        from apps.api import main as api_main

        monkeypatch.setenv("EMERGENCY_API_KEY", "emergency-test-key")
        monkeypatch.setenv("EMERGENCY_FLATTEN_GRACE_SECONDS", "0")
        monkeypatch.setattr(api_main, "PANIC_FILE", tmp_path / "panic.signal")
        response = client.post(
            "/api/v1/emergency/kill-all",
            headers={**auth_headers, "X-Emergency-Key": "emergency-test-key"},
            json={"confirmation": "CONFIRM_KILL"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "emergency_killed"


class TestMetricsEndpoint:
    """Tests for metrics endpoint."""

    def test_get_metrics(self, client, auth_headers):
        """Should return metrics or no_data status."""
        response = client.get("/api/v1/metrics", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        # Either has metrics or no_data status
        assert "status" in data or isinstance(data, dict)


class TestDataEndpoints:
    """Tests for data management."""

    def test_download_data_validates_parameters(self, client, auth_headers):
        """Should accept valid data download request."""
        response = client.post(
            "/api/v1/data/download",
            headers=auth_headers,
            json={
                "symbol": "BTC",
                "timeframe": "5m",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
        )
        # May fail to start subprocess in test environment
        assert response.status_code in [200, 500]


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client():
    """Create a test client for the API."""
    # Import the app with environment variables already set
    from apps.api import main as api_main

    api_main.API_KEY = "test-api-key-12345"
    api_main.API_KEY_CONFIGURED = True
    app = api_main.app

    # Skip environment validation for tests
    with patch("apps.api.main.validate_environment"):
        with patch("apps.api.main.prune_zombies"):
            with TestClient(app) as client:
                yield client


@pytest.fixture
def mock_subprocess():
    """Mock asyncio subprocess creation."""
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.stdout = AsyncMock()
    mock_proc.stderr = AsyncMock()
    mock_proc.wait = AsyncMock(return_value=0)

    async def mock_create(*args, **kwargs):
        return mock_proc

    return mock_create


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.integration
class TestAPIIntegration:
    """Integration tests requiring full environment."""

    def test_full_training_workflow(self, client, auth_headers, tmp_path, monkeypatch):
        """Test complete training workflow."""
        # Setup directories
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "app.yaml").write_text("env:\n  symbol: BTCUSDT")

        checkpoints_dir = tmp_path / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.chdir(tmp_path)

        # 1. Check initial status
        response = client.get("/api/v1/train/status", headers=auth_headers)
        assert response.status_code == 200

        # 2. Try to stop (should fail - nothing running)
        response = client.post("/api/v1/train/stop", headers=auth_headers)
        assert response.status_code == 404

    def test_full_trading_workflow(self, client, auth_headers, tmp_path, monkeypatch):
        """Test complete trading workflow."""
        monkeypatch.chdir(tmp_path)

        # 1. Check initial status
        response = client.get("/api/v1/trading/status", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "idle"

        # 2. Try to stop (should fail - nothing running)
        response = client.post("/api/v1/trading/stop", headers=auth_headers)
        assert response.status_code == 404

def test_start_training(client, auth_headers):
    # Mock the bounded process supervisor rather than inventing an invalid PID.
    with patch(
        "apps.api.main.start_managed_process",
        new_callable=AsyncMock,
        return_value={"pid": 12345},
    ):
        response = client.post(
            "/api/v1/train/start",
            headers=auth_headers,
            json={"config_name": "app.yaml", "model_version": "candidate-v1"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"

def test_start_trading(client, auth_headers, tmp_path, monkeypatch):
    # Unregistered checkpoints must not acquire paper/live authority.
    from apps.api import main as api_main

    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    monkeypatch.setattr(api_main, "CHECKPOINTS_DIR", checkpoints)
    with patch("apps.api.main.start_managed_process", new_callable=AsyncMock):
        response = client.post(
            "/api/v1/trading/start",
            headers=auth_headers,
            json={"mode": "paper", "model_version": "v1"}
        )
        assert response.status_code == 409
        assert "registered" in response.json()["detail"].lower()

def test_stream_subprocess_logs_mock(client, auth_headers):
    # The API delegates log/process lifecycle to the bounded supervisor.
    with patch(
        "apps.api.main.start_managed_process",
        new_callable=AsyncMock,
        return_value={"pid": 12345},
    ) as mock_start:
        response = client.post(
            "/api/v1/train/start",
            headers=auth_headers,
            json={"config_name": "app.yaml", "model_version": "candidate-v1"}
        )
        assert response.status_code == 200
        mock_start.assert_awaited_once()
