from trading_gym.utils.memory import (
    get_memory_usage_mb,
    check_memory_growth,
    RingBuffer,
    BoundedDict,
)


def test_ring_buffer():
    rb = RingBuffer(maxlen=3)
    rb.append(1)
    rb.append(2)
    rb.append(3)
    assert len(rb) == 3

    rb.append(4)
    assert len(rb) == 3
    assert list(rb) == [2, 3, 4]


def test_bounded_dict():
    bd = BoundedDict(maxsize=3)
    bd["a"] = 1
    bd["b"] = 2
    bd["c"] = 3
    assert len(bd) == 3

    bd["d"] = 4
    # "a" should be evicted
    assert "a" not in bd
    assert "d" in bd
    assert len(bd) == 3

    # Access "b", so "c" becomes LRU
    _ = bd["b"]
    bd["e"] = 5
    assert "c" not in bd
    assert "b" in bd


def test_get_memory_usage_mb():
    usage = get_memory_usage_mb()
    assert isinstance(usage, float)
    # Check for valid reading if psutil installed
    if usage != -1.0:
        assert usage > 0


def test_check_memory_growth():
    baseline = 100.0
    current = 150.0
    # 50% growth > 20%
    assert check_memory_growth(baseline, current, threshold_pct=20.0)

    current_small = 110.0
    assert not check_memory_growth(baseline, current_small, threshold_pct=20.0)
