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


@pytest.mark.parametrize(
    "bad_state",
    [
        {"revision": 1},
        {"halted": 0, "revision": 1},
        {"halted": False, "revision": "1"},
        {"halted": False, "revision": -1},
        {"halted": False, "revision": True},
        {"halted": False, "revision": 1, "reason": ["not a string"]},
    ],
)
def test_invalid_durable_emergency_state_never_defaults_to_unhalted(
    tmp_path, bad_state
) -> None:
    """A corrupt persisted HALT marker must not silently become an unhalted state."""
    import json

    state_file = tmp_path / "emergency.json"
    state_file.write_text(json.dumps(bad_state))
    with pytest.raises(ValueError, match="invalid_emergency_state"):
        EmergencyController(state_file).snapshot()


def test_dangling_emergency_state_symlink_is_not_missing_state(tmp_path) -> None:
    state_file = tmp_path / "emergency.json"
    try:
        state_file.symlink_to(tmp_path / "missing-state.json")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(ValueError, match="symlink"):
        EmergencyController(state_file).snapshot()


def test_oversized_emergency_state_refuses_to_read(tmp_path) -> None:
    state_file = tmp_path / "emergency.json"
    state_file.write_text(
        '{"halted": true, "revision": 1, "reason": "' + "x" * 65536 + '"}'
    )
    with pytest.raises(ValueError, match="invalid_emergency_state"):
        EmergencyController(state_file).snapshot()
