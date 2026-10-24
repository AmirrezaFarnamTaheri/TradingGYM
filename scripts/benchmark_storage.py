"""Measure the current JSON implementation on disposable synthetic fixtures.

No application home, live journals, financial records or audit anchors are read
or changed. Results describe this machine/workload; they are not a capacity SLA.
"""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time

from trading_gym.common.persistence import atomic_write_json, read_json


def benchmark(counts: list[int], repeats: int = 5) -> dict:
    if not 1 <= repeats <= 30 or any(not 1 <= count <= 100_000 for count in counts):
        raise ValueError("fixture counts/repeats outside benchmark bounds")
    rows = []
    with tempfile.TemporaryDirectory(prefix="tradinggym-storage-bench-") as temporary:
        path = Path(temporary) / "fixture.json"
        for count in counts:
            payload = {"records": {str(i): {"state": "succeeded", "revision": 3} for i in range(count)}}
            atomic_write_json(path, payload)
            elapsed = []
            for _ in range(repeats):
                start = time.perf_counter_ns()
                observed = read_json(path, {})
                elapsed.append((time.perf_counter_ns() - start) / 1_000_000)
                assert len(observed["records"]) == count
            rows.append({"records": count, "bytes": path.stat().st_size,
                         "read_ms_median": statistics.median(elapsed),
                         "read_ms_max": max(elapsed), "repeats": repeats})
    return {"scope": "synthetic_single_reader_current_json", "contention_measured": False,
            "integrity_validation_measured": False, "measurements": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, nargs="+", default=[100, 1000, 10000])
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.records, args.repeats), indent=2))
