"""Benchmark optional Rust ADX against the unchanged Python reference, offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def benchmark(bars: int, repeats: int, library: object) -> dict[str, object]:
    import numpy as np
    import pandas as pd
    from trading_gym.playground import kernel
    from trading_gym.strategy_lab.adx import compute_adx

    rng = np.random.default_rng(917)
    close = 100 + np.cumsum(rng.normal(0, 0.05, bars))
    frame = pd.DataFrame({"high": close + 0.2, "low": close - 0.2, "close": close})
    with patch.object(kernel, "load_kernel", return_value=None):
        reference = compute_adx(frame)
    with patch.object(kernel, "load_kernel", return_value=library):
        native = compute_adx(frame)
    if not np.array_equal(
        reference.to_numpy().view(np.uint64), native.to_numpy().view(np.uint64)
    ):
        raise ValueError("native ADX differs from the Python reference")
    samples: dict[str, list[float]] = {"python": [], "rust": []}
    for attempt in range(repeats):
        order = ("python", "rust") if attempt % 2 == 0 else ("rust", "python")
        for backend in order:
            with patch.object(
                kernel,
                "load_kernel",
                return_value=None if backend == "python" else library,
            ):
                start = time.perf_counter()
                compute_adx(frame)
                samples[backend].append(time.perf_counter() - start)
    python_time = statistics.median(samples["python"])
    rust_time = statistics.median(samples["rust"])
    return {
        "bars": bars,
        "period": 14,
        "repeats": repeats,
        "bit_identical": True,
        "python_median_seconds": python_time,
        "rust_median_seconds": rust_time,
        "speedup": python_time / rust_time,
        "samples_seconds": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20 or any(
        not 15 <= bars <= 5_000_000 for bars in args.bars
    ):
        parser.error("bars must be 15..5000000 and repeat must be 1..20")
    if len(args.bars) > 10:
        parser.error("at most 10 workloads are supported per invocation")
    from trading_gym.playground import kernel

    library = kernel.load_kernel()
    if library is None or not hasattr(library, "axiom_wilder_adx"):
        parser.error("ADX kernel unavailable; build rust/axiom_kernel in release mode")
    results = [benchmark(bars, args.repeat, library) for bars in args.bars]
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "scope": "synthetic offline ADX; includes conversion and FFI; excludes compilation and initial library load",
                "results": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
