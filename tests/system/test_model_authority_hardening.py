from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from trading_gym.core.legacy_api import main as api_main
from trading_gym.rl.trainer import update as trainer_update
from trading_gym.rl.models.versioning import ModelRegistry


def _registered(
    root: Path, tmp_path: Path, *, version: str = "v1", active: bool = False
) -> ModelRegistry:
    artifact = tmp_path / f"{version}.zip"
    artifact.write_bytes(f"artifact-{version}".encode())
    registry = ModelRegistry(root)
    registry.register(
        version,
        artifact,
        {"sharpe": 1.0},
        {"seed": 7},
        tags={"lifecycle": "candidate"},
        set_active=active,
    )
    return registry


def test_legacy_checkpoint_is_visible_but_not_authoritative(
    tmp_path: Path, monkeypatch
) -> None:
    checkpoints = tmp_path / "checkpoints"
    legacy = checkpoints / "legacy-v1"
    legacy.mkdir(parents=True)
    (legacy / "best_model.zip").write_bytes(b"legacy")
    (legacy / "metadata.json").write_text('{"reward": 100}', encoding="utf-8")
    monkeypatch.setattr(api_main, "CHECKPOINTS_DIR", checkpoints)

    listing = asyncio.run(api_main.list_models(api_key="test"))
    record = next(item for item in listing["models"] if item["version"] == "legacy-v1")
    assert record["status"] == "legacy_unregistered"
    assert record["authoritative"] is False
    assert record["activation_allowed"] is False

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            api_main.start_trading(
                api_main.TradingStartRequest(mode="paper", model_version="legacy-v1"),
                api_key="test",
                actor="operator",
                approval_key=None,
            )
        )
    assert error.value.status_code == 409


def test_registered_paper_model_uses_verified_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    checkpoints = tmp_path / "checkpoints"
    registry = _registered(checkpoints, tmp_path)
    expected = registry.artifact_path("v1")
    captured: dict[str, object] = {}

    async def fake_start(process_key, command, **kwargs):  # type: ignore[no-untyped-def]
        captured["command"] = command
        captured["metadata"] = kwargs["metadata"]
        return {"pid": 101}

    monkeypatch.setattr(api_main, "CHECKPOINTS_DIR", checkpoints)
    monkeypatch.setattr(api_main, "start_managed_process", fake_start)
    result = asyncio.run(
        api_main.start_trading(
            api_main.TradingStartRequest(mode="paper", model_version="v1"),
            api_key="test",
            actor="operator",
            approval_key=None,
        )
    )
    assert result["status"] == "started"
    command = captured["command"]
    assert isinstance(command, list)
    assert str(expected) in command
    assert (
        captured["metadata"]["artifact_sha256"] == registry.get_version("v1").code_hash
    )


def test_live_model_must_be_active(tmp_path: Path, monkeypatch) -> None:
    checkpoints = tmp_path / "checkpoints"
    _registered(checkpoints, tmp_path, active=False)
    monkeypatch.setattr(api_main, "CHECKPOINTS_DIR", checkpoints)
    request = api_main.TradingStartRequest(
        mode="live",
        model_version="v1",
        proposal_id="a" * 32,
        expected_revision=1,
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            api_main.start_trading(
                request,
                api_key="test",
                actor="operator",
                approval_key="approval",
            )
        )
    assert error.value.status_code == 409
    assert "active admitted model" in error.value.detail


def test_registry_activation_is_audited_and_tamper_blocks_resolution(
    tmp_path: Path,
) -> None:
    registry = _registered(tmp_path / "registry", tmp_path, active=False)
    registry.set_active("v1", actor="reviewer", reason="approved test")
    assert registry.get_active().version == "v1"
    assert registry.audit_pending() == 0

    artifact = registry.artifact_path("v1")
    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="size mismatch|checksum mismatch"):
        registry.artifact_path("v1")


def test_candidate_gate_requires_multiple_folds_and_material_improvement() -> None:
    insufficient = trainer_update.assess_candidate(
        SimpleNamespace(sharpe_ratio=1.0, evaluation_folds=1),
        SimpleNamespace(sharpe_ratio=1.5, evaluation_folds=1),
    )
    assert insufficient.qualified_for_staging is False
    assert any("insufficient" in reason for reason in insufficient.reasons)

    marginal = trainer_update.assess_candidate(
        SimpleNamespace(sharpe_ratio=1.0, evaluation_folds=4),
        SimpleNamespace(sharpe_ratio=1.04, evaluation_folds=4),
    )
    assert marginal.qualified_for_staging is False

    qualified = trainer_update.assess_candidate(
        SimpleNamespace(sharpe_ratio=1.0, evaluation_folds=5),
        SimpleNamespace(sharpe_ratio=1.2, evaluation_folds=5),
    )
    assert qualified.qualified_for_staging is True
    assert qualified.absolute_improvement == pytest.approx(0.2)


def test_update_can_stage_but_never_promotes_production(monkeypatch) -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def transition_model_version_stage(
            self, *, name: str, version: str, stage: str
        ) -> None:
            self.calls.append((name, version, stage))

    def load_model(uri: str) -> str:
        return "challenger" if "challenger" in uri else "champion"

    def evaluate(cfg, model):  # type: ignore[no-untyped-def]
        del cfg
        if model == "challenger":
            return SimpleNamespace(
                sharpe_ratio=1.3,
                evaluation_folds=5,
                model_name="strategy-model",
                model_version="2",
            )
        return SimpleNamespace(sharpe_ratio=1.0, evaluation_folds=5)

    client = Client()
    monkeypatch.setattr(trainer_update, "load_app_config", lambda _: object())
    monkeypatch.setattr(trainer_update, "run_walkforward", evaluate)
    assessment = trainer_update.update(
        Path("config/app.yaml"),
        "champion",
        "challenger",
        client=client,
        _mlflow_load_model=load_model,
    )
    assert assessment.qualified_for_staging is True
    assert client.calls == [("strategy-model", "2", "Staging")]
    assert all(stage != "Production" for _, _, stage in client.calls)


def test_training_entrypoint_targets_callable_main() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'tg-train = "trading_gym.rl.trainer.main:main"' in pyproject


def test_default_mlflow_deserialization_requires_explicit_acknowledgement(
    monkeypatch,
) -> None:
    monkeypatch.setattr(trainer_update, "load_app_config", lambda _: object())
    with pytest.raises(RuntimeError, match="disabled by default"):
        trainer_update.update(
            Path("config/app.yaml"),
            "a" * 32,
            "b" * 32,
            client=object(),  # type: ignore[arg-type]
        )
