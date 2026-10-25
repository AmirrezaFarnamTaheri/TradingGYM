"""Bitwise parity and fallback contracts for the optional Rust ADX recurrence."""

import math
import os
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.playground import kernel
from trading_gym.agent.strategy_lab.adx import compute_adx


def frame(seed: int, rows: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.1, rows))
    return pd.DataFrame(
        {
            "high": close + rng.uniform(0.01, 0.4, rows),
            "low": close - rng.uniform(0.01, 0.4, rows),
            "close": close,
        }
    )


def native_library():
    library = kernel.load_kernel()
    if library is None:
        if os.getenv("AXIOM_KERNEL_PATH") and os.getenv("AXIOM_KERNEL_DISABLE") != "1":
            pytest.fail(
                "explicit compiled kernel could not be loaded; native parity is required"
            )
        pytest.skip("compiled optional kernel unavailable")
    assert hasattr(library, "axiom_wilder_adx"), "rebuild the kernel with ADX support"
    return library


@pytest.mark.parametrize(
    "seed,rows,period",
    [(2, 100, 2), (7, 300, 14), (91, 510, 37), (4, 51, 50), (3, 100, 50)],
)
@pytest.mark.parametrize("component", ["adx", "plus_di", "minus_di", "dx"])
def test_native_adx_is_bit_identical_to_reference(seed, rows, period, component):
    library = native_library()
    data = frame(seed, rows)
    original = data.copy(deep=True)
    with patch.object(kernel, "load_kernel", return_value=None):
        reference = compute_adx(data, period=period, component=component)
    with patch.object(kernel, "load_kernel", return_value=library):
        actual = compute_adx(data, period=period, component=component)
    assert np.array_equal(
        reference.to_numpy().view(np.uint64), actual.to_numpy().view(np.uint64)
    )
    pd.testing.assert_frame_equal(data, original)
    assert actual.index.equals(data.index)
    assert actual.name == component


def test_old_library_and_missing_kernel_preserve_reference_fallback():
    data = frame(1, 120)
    with patch.object(kernel, "load_kernel", return_value=None):
        reference = compute_adx(data)
    with patch.object(kernel, "load_kernel", return_value=object()):
        actual = compute_adx(data)
    pd.testing.assert_series_equal(actual, reference)


def test_native_recurrence_accepts_noncontiguous_inputs_without_mutation():
    native_library()
    from trading_gym.agent.playground.kernel import wilder_adx

    values = np.arange(1.0, 40.0)[::2]
    before = values.copy()
    result = wilder_adx(
        true_range=values,
        moves_plus=values,
        moves_minus=np.zeros_like(values),
        period=3,
    )
    assert result is not None
    assert len(result) == 4
    assert all(len(item) == len(values) + 1 for item in result)
    assert np.array_equal(values, before)


@pytest.mark.parametrize(
    "case", ["length", "dimensions", "nan", "negative", "period", "short"]
)
def test_bridge_rejects_invalid_input_before_ffi(case):
    native_library()
    from trading_gym.agent.playground.kernel import KernelError, wilder_adx

    values = np.ones(20)
    kwargs = dict(true_range=values, moves_plus=values, moves_minus=values, period=3)
    if case == "length":
        kwargs["moves_plus"] = np.ones(19)
    elif case == "dimensions":
        kwargs["true_range"] = np.ones((20, 1))
    elif case == "nan":
        kwargs["true_range"] = np.full(20, np.nan)
    elif case == "negative":
        kwargs["true_range"] = np.full(20, -1.0)
    elif case == "period":
        kwargs["period"] = True
    else:
        kwargs["period"] = 30
    with pytest.raises(KernelError):
        wilder_adx(**kwargs)


@pytest.mark.parametrize("scale", [1e-8, 1.0, 1e15])
def test_randomized_prices_and_components_preserve_every_output_bit(scale):
    library = native_library()
    for seed in range(12):
        data = frame(seed, 301) * scale
        for component in ("adx", "plus_di", "minus_di", "dx"):
            with patch.object(kernel, "load_kernel", return_value=None):
                reference = compute_adx(data, period=2 + seed, component=component)
            with patch.object(kernel, "load_kernel", return_value=library):
                actual = compute_adx(data, period=2 + seed, component=component)
            assert np.array_equal(
                reference.to_numpy().view(np.uint64), actual.to_numpy().view(np.uint64)
            )


def test_overflow_rejection_matches_python_without_publishing_partial_results():
    library = native_library()
    data = pd.DataFrame(
        {
            "high": [5e307, 5e307, 5e307, 1.79e308, 1.79e308, 1.79e308],
            "low": [0.0] * 6,
            "close": [0.0] * 6,
        }
    )
    for loaded in (None, library):
        with patch.object(kernel, "load_kernel", return_value=loaded):
            with np.errstate(over="ignore", invalid="ignore"):
                with pytest.raises(
                    ValueError, match="ADX smoothed observations overflow"
                ):
                    compute_adx(data, period=2)


def test_bridge_aligns_unaligned_numpy_views_before_native_access():
    import ctypes
    from types import SimpleNamespace

    storage = np.zeros(20 * 8 + 1, dtype=np.uint8)
    values = np.ndarray((20,), dtype=np.float64, buffer=storage, offset=1)
    values[:] = 1.0
    assert not values.flags.aligned
    before = values.copy()

    def verify_alignment(*arguments):
        for pointer in arguments[:3]:
            address = ctypes.cast(pointer, ctypes.c_void_p).value
            assert address % ctypes.alignment(ctypes.c_double) == 0
        return 0

    with patch.object(
        kernel,
        "load_kernel",
        return_value=SimpleNamespace(axiom_wilder_adx=verify_alignment),
    ):
        result = kernel.wilder_adx(
            true_range=values, moves_plus=values, moves_minus=values, period=3
        )
    assert result is not None
    assert np.array_equal(values, before)


def test_negative_native_status_never_silently_falls_back():
    from types import SimpleNamespace

    broken = SimpleNamespace(axiom_wilder_adx=lambda *args: -5)
    with patch.object(kernel, "load_kernel", return_value=broken):
        with pytest.raises(ValueError, match="ADX smoothed observations overflow"):
            compute_adx(frame(2, 80))


def test_native_flat_market_preserves_nan_warmup_and_zero_direction():
    native_library()
    data = pd.DataFrame({name: [100.0] * 70 for name in ("high", "low", "close")})
    for component in ("adx", "plus_di", "minus_di", "dx"):
        with patch.object(kernel, "load_kernel", return_value=None):
            reference = compute_adx(data, component=component)
        actual = compute_adx(data, component=component)
        assert np.array_equal(
            reference.to_numpy().view(np.uint64), actual.to_numpy().view(np.uint64)
        )
        assert math.isfinite(actual.iloc[-1])
        assert actual.iloc[-1] == 0
