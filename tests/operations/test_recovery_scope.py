from unittest.mock import Mock
import pytest
from trading_gym.operations import resilience


def test_mixed_snapshot_can_pass_hashes_but_never_recovery_gate(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.json').write_text('{"revision":0}')
    (source / 'b.json').write_text('{"revision":0}')
    service = resilience.SnapshotService(source_root=source,
        backup_root=tmp_path / 'backups', ledger=Mock(), hmac_key=b'x' * 32)
    original = resilience.shutil.copyfileobj
    calls = 0

    def interleaved(reader, writer, **kwargs):
        nonlocal calls
        original(reader, writer, **kwargs)
        calls += 1
        if calls == 1:
            (source / 'a.json').write_text('{"revision":1}')
            (source / 'b.json').write_text('{"revision":1}')

    monkeypatch.setattr(resilience.shutil, 'copyfileobj', interleaved)
    snapshot = service.create(['a.json', 'b.json'])
    monkeypatch.setattr(resilience.shutil, 'copyfileobj', original)
    data = service.backup_root / snapshot.snapshot_id / 'data'
    assert (data / 'a.json').read_text() != (data / 'b.json').read_text()
    verified = service.verify(snapshot.snapshot_id)
    assert verified['valid'] is True
    assert verified['application_recovery_qualified'] is False
    assert verified['writer_quiescence_verified'] is False
    with pytest.raises(resilience.SnapshotConflict, match='not application-recovery qualified'):
        service.require_application_recovery(snapshot.snapshot_id)
    drill = service.drill_restore(snapshot.snapshot_id)
    assert drill.valid and not drill.application_recovery_qualified
    assert drill.validation_scope == 'byte_integrity_only'
    plan = service.plan_restore(snapshot.snapshot_id)
    assert not plan.application_recovery_qualified
    assert (source / 'a.json').read_text() == '{"revision":1}'


def test_corruption_remains_a_failed_byte_check(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'state.json').write_text('{}')
    service = resilience.SnapshotService(source_root=source,
        backup_root=tmp_path / 'backup', ledger=Mock(), hmac_key=b'x' * 32)
    snapshot = service.create(['state.json'])
    (service.backup_root / snapshot.snapshot_id / 'data' / 'state.json').write_text('broken')
    with pytest.raises(resilience.SnapshotConflict, match='byte verification failed'):
        service.require_application_recovery(snapshot.snapshot_id)
    with pytest.raises(ValueError, match='verification failed'):
        service.drill_restore(snapshot.snapshot_id)
