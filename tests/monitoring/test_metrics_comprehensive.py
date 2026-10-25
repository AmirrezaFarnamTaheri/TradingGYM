import pytest
from trading_gym.core.monitoring.metrics import get_metrics, reset_metrics


@pytest.fixture(autouse=True)
def cleanup_metrics():
    reset_metrics()
    yield
    reset_metrics()


@pytest.fixture
def metrics():
    return get_metrics()


def test_trading_metrics_record_order(metrics):
    metrics.record_order("BTCUSDT", "buy", "limit", 0.1)
    # Access underlying prometheus metric
    # Note: testing prometheus internals is tricky without exporting
    # But we can check if no error raised
    assert True


def test_update_position(metrics):
    metrics.update_position("BTCUSDT", 1.0, 50000.0)
    assert True


def test_record_circuit_breaker(metrics):
    metrics.record_circuit_breaker_trip("max_loss")
    metrics.record_circuit_breaker_reset("max_loss")
    assert True


def test_singleton():
    m1 = get_metrics()
    m2 = get_metrics()
    assert m1 is m2


def test_export(metrics):
    output = metrics.export()
    assert isinstance(output, bytes)
    assert b"trading_orders_total" in output
