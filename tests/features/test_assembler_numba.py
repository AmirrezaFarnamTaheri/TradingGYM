import numpy as np
from trading_gym.features.feature_assembler import (
    calculate_rsi,
    calculate_log_returns,
    calculate_volatility,
)


def test_log_returns_numba():
    prices = np.array([100.0, 101.0, 102.01], dtype=np.float64)
    # ln(101/100) approx 0.00995
    # ln(102.01/101) = ln(1.01) approx 0.00995
    logs = calculate_log_returns(prices)

    assert logs[0] == 0.0
    assert np.allclose(logs[1], np.log(1.01))
    assert np.allclose(logs[2], np.log(1.01))


def test_volatility_numba():
    # Standard deviation of [1, 1, 1] is 0
    returns = np.array([0.01, 0.01, 0.01, 0.01, 0.01], dtype=np.float64)
    window = 3
    vol = calculate_volatility(returns, window)

    assert vol[0] == 0.0  # First 'window' elements are 0 init
    assert vol[window] == 0.0  # First calculated window

    # Standard deviation of [1, -1] is 1.0
    returns_volatile = np.array([1.0, -1.0, 1.0, -1.0], dtype=np.float64)
    vol_v = calculate_volatility(returns_volatile, window=2)

    # std([1, -1]) = 1.0
    assert np.allclose(vol_v[2], 1.0)


def test_rsi_numba():
    # 14 period RSI
    # Flat line -> RSI 50 (neutral) or undefined?
    # If gain=0, loss=0. 0/0.
    # Implementation uses 1e-9 epsilon. So 0/epsilon = 0 gain/loss.
    # Formula: 100 - 100/(1+RS). If RS=0, RSI=0? Or 50?
    # Implementation says: rsi[:period] = 50.0

    prices = np.full(100, 100.0, dtype=np.float64)
    rsi = calculate_rsi(prices, period=14)

    # No gain and no loss is neutral, not maximally oversold.
    assert np.allclose(rsi[20], 50.0)

    # Uptrend
    prices_up = np.linspace(100, 200, 100, dtype=np.float64)
    rsi_up = calculate_rsi(prices_up, period=14)

    # Should be high (near 100)
    assert rsi_up[20] > 90.0
