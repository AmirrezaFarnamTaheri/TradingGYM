from trading_gym.operations.storage_health import StoreSpec, inspect_store, storage_health


def test_size_thresholds_are_not_integrity_verdicts(tmp_path):
    spec = StoreSpec('fixture', 'fixture.json', 100)
    assert inspect_store(tmp_path, spec)['status'] == 'absent'
    path = tmp_path / 'fixture.json'
    path.write_bytes(b'x' * 80)  # Deliberately not valid JSON: only stat is inspected.
    row = inspect_store(tmp_path, spec)
    assert row['status'] == 'near_limit' and row['headroom_bytes'] == 20
    assert row['integrity_checked'] is False
    path.write_bytes(b'x' * 101)
    assert inspect_store(tmp_path, spec)['status'] == 'limit_reached'
    assert path.read_bytes() == b'x' * 101


def test_inventory_bound_and_symlink_fail_closed(tmp_path):
    accounts = tmp_path / 'accounting' / 'accounts'
    accounts.mkdir(parents=True)
    for i in range(3):
        (accounts / f'{i}.json').write_text('{}')
    result = storage_health(tmp_path, max_accounts=2)
    assert result['attention_required']
    assert result['account_inventory'] == 'bounded_inventory_exhausted'
    (tmp_path / 'proposals.json').symlink_to(accounts / '0.json')
    assert inspect_store(tmp_path, StoreSpec('proposals', 'proposals.json', 100))['status'] == 'unavailable'


def test_new_account_artifact_and_sqlite_surfaces_have_explicit_coverage(tmp_path):
    for relative in ('paper/BTC/account.json', 'example-runs/fixture/result.json',
                     'intelligence/decisions.sqlite3', 'intelligence/decisions.sqlite3-wal'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    report = storage_health(tmp_path)
    rows = {row['name']: row for row in report['stores']}
    assert rows['paper/BTC/account.json']['limit_bytes'] == 16 * 1024**2
    assert rows['example-runs/fixture/result.json']['limit_bytes'] == 512 * 1024
    assert rows['decision-journal']['status'] == 'limit_unconfigured'
    assert rows['decision-journal-wal']['size_bytes'] == 7
    assert report['coverage'] == 'partial' and report['attention_required']
    assert report['uninspected_inventories']
