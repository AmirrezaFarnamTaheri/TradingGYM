from datetime import datetime, timedelta, timezone

import pytest

from trading_gym.operations.incidents import (
    IncidentActionItem, IncidentConflict, IncidentCreate, IncidentEvent, IncidentEventType,
    IncidentRepository, IncidentRole, IncidentSeverity, IncidentState, evaluate_incident_readiness,
)


def create(repo: IncidentRepository):
    now = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
    return repo.create(IncidentCreate(
        incident_id="inc-platform-001", severity=IncidentSeverity.SEV1,
        title="Order reconciliation unavailable", summary="Broker state cannot be reconciled",
        detected_at=now, detected_by="monitor", affected_services=["orders", "accounting"],
    ))


def event(snapshot, suffix, event_type, at, **kwargs):
    return IncidentEvent(
        event_id=f"incident-event-platform-{suffix}", incident_id=snapshot.incident_id,
        event_type=event_type, occurred_at=at, actor="operator", **kwargs,
    )


def test_incident_state_machine_roles_actions_and_no_mitigation_authority(tmp_path):
    repo = IncidentRepository(tmp_path / "incidents.json")
    snapshot = create(repo)
    at = snapshot.updated_at + timedelta(minutes=1)
    snapshot = repo.append(event(snapshot, "role-001", IncidentEventType.ASSIGN_ROLE, at, role=IncidentRole.COMMANDER, assignee="alice"), expected_revision=snapshot.revision)
    for index, transition in enumerate((IncidentEventType.ACKNOWLEDGE, IncidentEventType.START_INVESTIGATION, IncidentEventType.START_MITIGATION, IncidentEventType.START_MONITORING, IncidentEventType.RESOLVE, IncidentEventType.START_POSTMORTEM), start=2):
        snapshot = repo.append(event(snapshot, f"transition-{index:03d}", transition, at + timedelta(minutes=index)), expected_revision=snapshot.revision)
    action = IncidentActionItem(action_id="action-fix-001", description="Add reconciliation failover", owner="bob", due_at=at + timedelta(days=7))
    snapshot = repo.append(event(snapshot, "action-add-001", IncidentEventType.ADD_ACTION_ITEM, at + timedelta(minutes=10), action_item=action), expected_revision=snapshot.revision)
    with pytest.raises(IncidentConflict):
        repo.append(event(snapshot, "close-too-soon", IncidentEventType.CLOSE, at + timedelta(minutes=11)), expected_revision=snapshot.revision)
    snapshot = repo.append(event(snapshot, "action-done-001", IncidentEventType.COMPLETE_ACTION_ITEM, at + timedelta(minutes=12), action_id=action.action_id), expected_revision=snapshot.revision)
    snapshot = repo.append(event(snapshot, "close-001", IncidentEventType.CLOSE, at + timedelta(minutes=13)), expected_revision=snapshot.revision)
    assert snapshot.state is IncidentState.CLOSED
    assert snapshot.mitigation_authority_granted is False
    assert snapshot.deployment_authority_granted is False
    assert repo.get(snapshot.incident_id) == snapshot


def test_severe_incident_requires_commander_and_cas(tmp_path):
    repo = IncidentRepository(tmp_path / "incidents.json")
    snapshot = create(repo)
    at = snapshot.updated_at + timedelta(minutes=1)
    acknowledged = repo.append(event(snapshot, "ack-001", IncidentEventType.ACKNOWLEDGE, at), expected_revision=snapshot.revision)
    investigating = repo.append(event(acknowledged, "investigate-001", IncidentEventType.START_INVESTIGATION, at + timedelta(minutes=1)), expected_revision=acknowledged.revision)
    with pytest.raises(IncidentConflict):
        repo.append(event(investigating, "mitigate-001", IncidentEventType.START_MITIGATION, at + timedelta(minutes=2)), expected_revision=investigating.revision)
    with pytest.raises(IncidentConflict):
        repo.append(event(investigating, "note-001", IncidentEventType.NOTE, at + timedelta(minutes=2), note="stale"), expected_revision=1)


def test_replay_detects_snapshot_tampering(tmp_path):
    path = tmp_path / "incidents.json"
    repo = IncidentRepository(path)
    snapshot = create(repo)
    import json
    raw = json.loads(path.read_text())
    raw["incidents"][snapshot.incident_id]["snapshot"]["title"] = "tampered"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="materialized snapshot"):
        repo.get(snapshot.incident_id)


def test_readiness_blocks_open_severe_missing_postmortem_and_overdue_actions(tmp_path):
    repo = IncidentRepository(tmp_path / "incidents.json")
    snapshot = create(repo)
    report = evaluate_incident_readiness([snapshot], evaluated_at=snapshot.detected_at + timedelta(days=2))
    assert report.ready is False
    assert report.open_sev1 == 1
    assert report.unassigned_commanders == [snapshot.incident_id]
    assert report.mitigation_authority_granted is False
