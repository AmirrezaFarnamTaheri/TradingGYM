import pytest
from trading_gym.rl.models.versioning import ModelRegistry, AutoRollback


@pytest.fixture
def registry_dir(tmp_path):
    return tmp_path / "registry"


@pytest.fixture
def registry(registry_dir):
    return ModelRegistry(registry_dir)


@pytest.fixture
def dummy_model(tmp_path):
    model_path = tmp_path / "dummy_model.zip"
    model_path.write_text("dummy model content")
    return model_path


def test_register_model(registry, dummy_model):
    registry.register(
        version="v1",
        model_path=dummy_model,
        metrics={"sharpe": 1.5},
        config={"env": "test"},
        tags={"env": "prod"},
    )

    meta = registry.get_version("v1")
    assert meta is not None
    assert meta.version == "v1"
    assert meta.metrics["sharpe"] == 1.5
    assert meta.tags["env"] == "prod"

    # Verify file copied
    assert (registry.registry_dir / "v1" / "dummy_model.zip").exists()

    # Verify metadata saved
    assert (registry.registry_dir / "v1" / "metadata.json").exists()


def test_get_active(registry, dummy_model):
    registry.register("v1", dummy_model, {}, {})
    active = registry.get_active()
    assert active.version == "v1"

    registry.register("v2", dummy_model, {}, {}, set_active=False)
    active = registry.get_active()
    assert active.version == "v1"

    registry.set_active("v2")
    active = registry.get_active()
    assert active.version == "v2"


def test_get_latest(registry, dummy_model):
    registry.register("v1", dummy_model, {}, {})
    import time

    time.sleep(0.01)  # Ensure timestamp difference
    # Tags are key-value pairs. get_latest(tag="key") checks if key exists.
    registry.register("v2", dummy_model, {}, {}, tags={"dev": "true"})

    latest = registry.get_latest()
    assert latest.version == "v2"

    # Filter by tag
    registry.register("v3", dummy_model, {}, {}, tags={"prod": "true"})

    latest_dev = registry.get_latest(tag="dev")
    assert latest_dev is not None
    assert latest_dev.version == "v2"

    latest_prod = registry.get_latest(tag="prod")
    assert latest_prod is not None
    assert latest_prod.version == "v3"


def test_delete_version(registry, dummy_model):
    registry.register("v1", dummy_model, {}, {}, set_active=False)
    assert registry.get_version("v1") is not None

    registry.delete_version("v1")
    assert registry.get_version("v1") is None
    assert not (registry.registry_dir / "v1").exists()


def test_delete_active_version_fails(registry, dummy_model):
    registry.register("v1", dummy_model, {}, {})

    with pytest.raises(ValueError, match="Cannot delete active version"):
        registry.delete_version("v1")

    # Force delete
    registry.delete_version("v1", force=True)
    assert registry.get_version("v1") is None


def test_compare_versions(registry, dummy_model):
    registry.register("v1", dummy_model, {"sharpe": 1.0}, {})
    registry.register("v2", dummy_model, {"sharpe": 1.5}, {})

    comparison = registry.compare_versions("v2", "v1", "sharpe")
    assert comparison["diff"] == 0.5
    assert comparison["pct_change"] == 50.0


def test_auto_rollback(registry, dummy_model):
    registry.register("v1", dummy_model, {"sharpe": 2.0}, {})
    registry.register("v2", dummy_model, {"sharpe": 1.0}, {})  # Degraded

    rollback = AutoRollback(
        registry, baseline_version="v1", metric="sharpe", threshold=0.8
    )

    # Current sharpe 1.0 vs baseline 2.0 = 0.5 < 0.8 -> Should rollback
    assert rollback.should_rollback(current_value=1.0)

    # Current sharpe 1.8 vs baseline 2.0 = 0.9 > 0.8 -> No rollback
    assert not rollback.should_rollback(current_value=1.8)

    # Execute rollback
    registry.set_active("v2")
    assert registry.get_active().version == "v2"

    rollback.execute()
    assert registry.get_active().version == "v1"
