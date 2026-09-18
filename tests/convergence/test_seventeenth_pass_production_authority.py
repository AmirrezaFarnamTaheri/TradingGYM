from pathlib import Path


def test_operator_workspace_surfaces_single_authority_and_non_custodial_posture() -> (
    None
):
    root = Path(__file__).resolve().parents[2]
    panel = (root / "frontend/src/components/ProductionPanel.jsx").read_text(
        encoding="utf-8"
    )
    app = (root / "frontend/src/App.jsx").read_text(encoding="utf-8")
    api = (root / "apps/api/production.py").read_text(encoding="utf-8")
    assert "Production Authority" in panel
    assert "ProductionPanel" in app
    assert "LiveExecutionCoordinator" in api
    assert "wallet_custody" in api
    assert "emergency" in api.lower()
