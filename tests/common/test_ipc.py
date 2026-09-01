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
