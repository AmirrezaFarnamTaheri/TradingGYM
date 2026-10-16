import pytest
import time
import os
from apps.common.ipc import MetricsWriter, MetricsReader


@pytest.fixture
def metrics_file(tmp_path):
    return str(tmp_path / "test_metrics.dat")


def test_writer_reader_roundtrip(metrics_file):
    # 1. Write
    writer = MetricsWriter(metrics_file, size=1024)
    data = {"cpu": 50.5, "equity": 10000.0, "status": "ok"}
    writer.write(data)

    # 2. Read
    reader = MetricsReader(metrics_file, size=1024)
    read_data = reader.read()

    assert read_data == data
    assert isinstance(read_data["cpu"], float)


def test_read_empty_file(metrics_file):
    # File doesn't exist
    if os.path.exists(metrics_file):
        os.remove(metrics_file)

    reader = MetricsReader(metrics_file, size=1024)
    data = reader.read()
    assert data == {}


def test_concurrent_update(metrics_file):
    writer = MetricsWriter(metrics_file, size=1024)
    reader = MetricsReader(metrics_file, size=1024)

    for i in range(5):
        writer.write({"count": i})
        time.sleep(0.01)
        read = reader.read()
        assert read["count"] == i


def test_reader_rejects_oversized_metrics_without_unbounded_json_parse(metrics_file):
    from apps.common.ipc import MAX_ENVELOPE_BYTES
    with open(metrics_file, "wb") as handle:
        handle.write(b"{" + b"x" * (MAX_ENVELOPE_BYTES + 10))
    reader = MetricsReader(metrics_file)
    assert reader.read() == {}
    assert reader.last_error == "ValueError"


@pytest.mark.parametrize("timestamp", ["NaN", "Infinity", "future"])
def test_reader_rejects_nonfinite_or_future_metric_clock(metrics_file, timestamp):
    import json
    writer = MetricsWriter(metrics_file)
    writer.write({"equity": 100})
    with open(metrics_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    data["written_at"] = time.time() + 300 if timestamp == "future" else timestamp
    with open(metrics_file, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    reader = MetricsReader(metrics_file)
    assert reader.read() == {}
    assert reader.last_error in {"ValueError", "future"}


def test_optional_metrics_file_recovers_and_clears_prior_error(metrics_file):
    reader = MetricsReader(metrics_file)
    assert reader.read() == {}
    assert reader.last_error == "missing"
    MetricsWriter(metrics_file).write({"ok": True})
    assert reader.read() == {"ok": True}
    assert reader.last_error is None
