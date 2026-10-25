from trading_gym.core.config_registry import ConfigRegistry


def test_config_registry_deep_composes_layers_and_hashes_snapshot() -> None:
    registry = ConfigRegistry(
        {"execution": {"max_leverage": 5, "enabled": True}, "mode": "paper"},
        {"execution": {"max_leverage": 3}},
    )
    snapshot = registry.snapshot()
    assert snapshot["config"] == {
        "execution": {"max_leverage": 3, "enabled": True},
        "mode": "paper",
    }
    assert len(snapshot["sha256"]) == 64
    assert snapshot["layer_count"] == 2
    assert registry.verify(snapshot["sha256"])
    assert not registry.verify("0" * 64)
