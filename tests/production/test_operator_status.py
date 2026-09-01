from trading_gym.production.status import build_status

def test_status_is_read_only():
    r=build_status({'service':'ok'})
    assert r['read_only'] and r['authority_granted'] is False
