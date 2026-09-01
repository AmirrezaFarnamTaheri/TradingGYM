import pytest

from trading_gym.production.emergency import EmergencyController


def test_emergency_control_is_revisioned_durable_and_two_actor(tmp_path) -> None:
    controller = EmergencyController(tmp_path / "emergency.json")
    halted = controller.transition(
        halt=True,
        reason="market integrity incident",
        expected_revision=0,
        actor="operator",
        approver="supervisor",
    )
    assert halted.halted and halted.revision == 1
    assert EmergencyController(tmp_path / "emergency.json").snapshot() == halted
    with pytest.raises(RuntimeError, match="revision_conflict"):
        controller.transition(
            halt=False,
            reason="stale",
            expected_revision=0,
            actor="operator",
            approver="supervisor",
        )
    with pytest.raises(PermissionError, match="independent_approval_required"):
        controller.transition(
            halt=False,
            reason="bad",
            expected_revision=1,
            actor="operator",
            approver="operator",
        )
