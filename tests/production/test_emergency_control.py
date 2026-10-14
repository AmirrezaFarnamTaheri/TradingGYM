import pytest

from trading_gym.production.emergency import (
    EmergencyController,
    EmergencyState,
    apply_emergency,
)


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


@pytest.mark.parametrize("halt", ["false", "true", 1, 0, None])
def test_emergency_transition_rejects_non_boolean_halt(halt: object) -> None:
    with pytest.raises(ValueError, match="halt_must_be_boolean"):
        apply_emergency(
            EmergencyState(),
            halt=halt,
            reason="reviewed",
            expected_revision=0,
            actor="operator-1",
            approver="approver-2",
        )


@pytest.mark.parametrize("revision", [True, 0.0, "0", -1])
def test_emergency_transition_rejects_coerced_revision(
    revision: object,
) -> None:
    with pytest.raises(ValueError, match="expected_revision_invalid"):
        apply_emergency(
            EmergencyState(),
            halt=True,
            reason="reviewed",
            expected_revision=revision,
            actor="operator-1",
            approver="approver-2",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "x" * 501),
        ("actor", "x" * 121),
        ("approver", "x" * 121),
    ],
)
def test_emergency_transition_rejects_unbounded_durable_fields(
    field: str, value: str
) -> None:
    arguments: dict[str, object] = {
        "halt": True,
        "reason": "reviewed",
        "expected_revision": 0,
        "actor": "operator-1",
        "approver": "approver-2",
    }
    arguments[field] = value
    with pytest.raises(ValueError):
        apply_emergency(EmergencyState(), **arguments)


def test_emergency_transition_preserves_valid_boolean_and_revision() -> None:
    next_state = apply_emergency(
        EmergencyState(),
        halt=True,
        reason="  reviewed  ",
        expected_revision=0,
        actor="  operator-1  ",
        approver=" approver-2 ",
    )
    assert next_state.halted is True
    assert next_state.revision == 1
    assert next_state.reason == "reviewed"
    assert next_state.actor == "operator-1"
    assert next_state.approver == "approver-2"
