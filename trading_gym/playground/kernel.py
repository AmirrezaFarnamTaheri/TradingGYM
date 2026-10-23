"""ctypes bridge to the Rust simulation kernel (ADR-035).

The playground's inner loop carries position state across bars, so it
cannot be vectorised; the strategy forge runs it (candidates × sealed
windows) times per generation, which makes it the platform's one
genuinely hot numeric path. ``rust/axiom_kernel`` reimplements exactly
that loop with the same expression association order, and this module
loads the compiled artifact through ``ctypes`` — no build-time
dependency, no extension module, nothing to import at install time.

Three rules keep the acceleration honest:

1. **Optional.** :func:`load_kernel` returns ``None`` when the library is
   absent or its ABI version is unknown. ``run_playground`` then uses the
   Python reference and behaviour is unchanged, so a machine without a
   Rust toolchain is a supported deployment, not a broken one.
2. **Reference-defined.** The Python loop is the semantics of record.
   ``tests/playground/test_kernel_parity.py`` asserts *bit-identical*
   equity curves and trade ledgers over randomised windows; a
   discrepancy is a kernel bug, never a new behaviour.
3. **Fail-closed.** The FFI validates lengths, capital, fees, fraction,
   and price finiteness, and returns a negative status rather than
   writing partial output; :func:`simulate` raises on any negative
   status instead of silently degrading.

Set ``AXIOM_KERNEL_DISABLE=1`` to force the Python path (used by the
parity test), or ``AXIOM_KERNEL_PATH`` to point at a specific artifact.
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "KernelError",
    "KernelResult",
    "kernel_available",
    "kernel_status",
    "load_kernel",
    "simulate",
    "wilder_adx",
]

_ABI_VERSION = 1
_ENV_PATH = "AXIOM_KERNEL_PATH"
_ENV_DISABLE = "AXIOM_KERNEL_DISABLE"

#: Cached load attempt: (library or None, human-readable reason).
_CACHE: tuple[Any, str] | None = None


class KernelError(RuntimeError):
    """The kernel rejected the inputs or could not produce a result."""


_STATUS_REASONS = {
    -1: "length mismatch between window columns",
    -2: "empty window",
    -3: "initial_capital must be positive and finite",
    -4: "fee_bps must be non-negative and finite",
    -5: "position_fraction must be within (0, 1]",
    -6: "window contains a non-finite or non-positive price",
    -7: "trade buffer capacity exceeded",
    -8: "null buffer passed to the kernel",
}


@dataclass(frozen=True)
class KernelResult:
    """One kernel simulation: equity curve, trades, exposure numerator."""

    equity: np.ndarray
    trades: tuple[dict[str, Any], ...]
    bars_in_position: int


def _candidate_paths() -> list[Path]:
    override = os.environ.get(_ENV_PATH)
    if override:
        return [Path(override)]
    if sys.platform.startswith("win"):
        names = ["axiom_kernel.dll"]
    elif sys.platform == "darwin":
        names = ["libaxiom_kernel.dylib"]
    else:
        names = ["libaxiom_kernel.so"]
    root = Path(__file__).resolve().parents[2] / "rust" / "axiom_kernel" / "target"
    return [root / profile / name for profile in ("release", "debug") for name in names]


def load_kernel() -> Any | None:
    """Return the loaded kernel, or ``None`` when unavailable.

    Never raises: an unavailable kernel is a supported state. The result
    (and the reason) is cached for the process.
    """

    global _CACHE
    if _CACHE is not None:
        return _CACHE[0]
    if os.environ.get(_ENV_DISABLE) == "1":
        _CACHE = (None, "disabled by AXIOM_KERNEL_DISABLE=1")
        return None
    for path in _candidate_paths():
        if not path.exists():
            continue
        try:
            library = ctypes.CDLL(str(path))
            library.axiom_kernel_abi_version.restype = ctypes.c_uint32
            version = int(library.axiom_kernel_abi_version())
            if version != _ABI_VERSION:
                _CACHE = (
                    None,
                    f"kernel at {path} reports ABI {version}, expected {_ABI_VERSION}",
                )
                return None
            library.axiom_simulate.restype = ctypes.c_int64
            library.axiom_simulate.argtypes = [
                ctypes.POINTER(ctypes.c_int8),  # intent
                ctypes.POINTER(ctypes.c_double),  # open
                ctypes.POINTER(ctypes.c_double),  # close
                ctypes.POINTER(ctypes.c_double),  # spread_bps
                ctypes.c_uint64,  # n
                ctypes.c_double,  # initial_capital
                ctypes.c_double,  # fee_bps
                ctypes.c_double,  # position_fraction
                ctypes.POINTER(ctypes.c_double),  # out_equity
                ctypes.POINTER(ctypes.c_uint64),  # out_entry_bar
                ctypes.POINTER(ctypes.c_uint64),  # out_exit_bar
                ctypes.POINTER(ctypes.c_double),  # out_entry_price
                ctypes.POINTER(ctypes.c_double),  # out_exit_price
                ctypes.POINTER(ctypes.c_double),  # out_pnl
                ctypes.POINTER(ctypes.c_double),  # out_cost
                ctypes.c_uint64,  # trade_capacity
                ctypes.POINTER(ctypes.c_uint64),  # out_bars_in_position
            ]
            adx_entrypoint = getattr(library, "axiom_wilder_adx", None)
            if adx_entrypoint is not None:
                adx_entrypoint.restype = ctypes.c_int64
                adx_entrypoint.argtypes = (
                    [ctypes.POINTER(ctypes.c_double)] * 3
                    + [ctypes.c_uint64] * 2
                    + [ctypes.c_double] * 3
                    + [ctypes.POINTER(ctypes.c_double)] * 4
                )
            _CACHE = (library, f"loaded {path}")
            return library
        except OSError as exc:
            _CACHE = (None, f"failed to load {path}: {exc}")
            return None
    _CACHE = (None, "no compiled kernel found (build rust/axiom_kernel)")
    return None


def kernel_available() -> bool:
    return load_kernel() is not None


def kernel_status() -> dict[str, Any]:
    """Diagnostics for the operator surface: available plus the reason."""

    library = load_kernel()
    assert _CACHE is not None
    return {
        "available": library is not None,
        "abi_version": _ABI_VERSION,
        "detail": _CACHE[1],
    }


def simulate(
    *,
    intent: np.ndarray,
    open_: np.ndarray,
    close: np.ndarray,
    spread_bps: np.ndarray,
    initial_capital: float,
    fee_bps: float,
    position_fraction: float,
) -> KernelResult | None:
    """Run the kernel, or return ``None`` when it is unavailable.

    Raises :class:`KernelError` when the kernel is present but rejects
    the inputs — a rejection is a real disagreement about validity, not a
    reason to silently fall back.
    """

    library = load_kernel()
    if library is None:
        return None

    n = int(intent.shape[0])
    if n == 0:
        raise KernelError("empty window")
    intent_buf = np.ascontiguousarray(intent, dtype=np.int8)
    open_buf = np.ascontiguousarray(open_, dtype=np.float64)
    close_buf = np.ascontiguousarray(close, dtype=np.float64)
    spread_buf = np.ascontiguousarray(spread_bps, dtype=np.float64)
    if not (open_buf.shape[0] == close_buf.shape[0] == spread_buf.shape[0] == n):
        raise KernelError("window columns must have equal length")

    equity = np.empty(n, dtype=np.float64)
    # One round trip needs an entry and an exit bar, so trades <= n // 2 + 1.
    capacity = n // 2 + 2
    entry_bar = np.empty(capacity, dtype=np.uint64)
    exit_bar = np.empty(capacity, dtype=np.uint64)
    entry_price = np.empty(capacity, dtype=np.float64)
    exit_price = np.empty(capacity, dtype=np.float64)
    pnl = np.empty(capacity, dtype=np.float64)
    cost = np.empty(capacity, dtype=np.float64)
    bars_in_position = ctypes.c_uint64(0)

    status = int(
        library.axiom_simulate(
            intent_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_int8)),
            open_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            close_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            spread_buf.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            ctypes.c_uint64(n),
            ctypes.c_double(float(initial_capital)),
            ctypes.c_double(float(fee_bps)),
            ctypes.c_double(float(position_fraction)),
            equity.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            entry_bar.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
            exit_bar.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
            entry_price.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            exit_price.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            pnl.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            cost.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            ctypes.c_uint64(capacity),
            ctypes.byref(bars_in_position),
        )
    )
    if status < 0:
        raise KernelError(
            _STATUS_REASONS.get(status, f"kernel returned status {status}")
        )

    trades = tuple(
        {
            "entry_bar": int(entry_bar[slot]),
            "exit_bar": int(exit_bar[slot]),
            "entry_price": float(entry_price[slot]),
            "exit_price": float(exit_price[slot]),
            "pnl_quote": float(pnl[slot]),
            "cost_quote": float(cost[slot]),
        }
        for slot in range(status)
    )
    return KernelResult(
        equity=equity,
        trades=trades,
        bars_in_position=int(bars_in_position.value),
    )


def wilder_adx(
    *,
    true_range: np.ndarray,
    moves_plus: np.ndarray,
    moves_minus: np.ndarray,
    period: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """Run the optional ADX recurrence, preserving Python fsum seeds and warmup.

    Old ABI-1 libraries without this additive symbol use the Python reference.
    Native errors invalidate all outputs and never silently fall back.
    """
    library = load_kernel()
    function = getattr(library, "axiom_wilder_adx", None)
    if function is None:
        return None
    if type(period) is not int or not 2 <= period <= 10_000:
        raise KernelError("invalid ADX period")
    buffers = tuple(
        np.require(values, dtype=np.float64, requirements=["C", "A"])
        for values in (true_range, moves_plus, moves_minus)
    )
    if any(values.ndim != 1 for values in buffers):
        raise KernelError("ADX inputs must be one-dimensional")
    n = len(buffers[0]) + 1
    if n <= period or any(len(values) + 1 != n for values in buffers):
        raise KernelError("ADX inputs must share a length and contain the seed period")
    if any(not np.isfinite(values).all() or (values < 0).any() for values in buffers):
        raise KernelError(
            "ADX smoothed observations overflow or contain invalid values"
        )
    seeds = tuple(math.fsum(values[:period]) for values in buffers)
    outputs = tuple(np.empty(n, dtype=np.float64) for _ in range(4))
    status = int(
        function(
            *(
                values.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
                for values in buffers
            ),
            ctypes.c_uint64(n),
            ctypes.c_uint64(period),
            *(ctypes.c_double(seed) for seed in seeds),
            *(
                values.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
                for values in outputs
            ),
        )
    )
    if status != 0:
        raise KernelError(
            f"ADX smoothed observations overflow or kernel rejected inputs ({status})"
        )
    adx, plus_di, minus_di, dx = outputs
    return adx, plus_di, minus_di, dx
