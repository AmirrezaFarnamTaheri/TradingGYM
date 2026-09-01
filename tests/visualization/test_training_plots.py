import pytest
from unittest.mock import patch
import pandas as pd
import numpy as np

# Use non-interactive backend to avoid Tk issues in CI/headless environments
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from trading_gym.visualization.training_plots import TrainingVisualizer  # noqa: E402


@pytest.fixture(autouse=True)
def cleanup_matplotlib():
    """Clean up matplotlib state after each test to prevent resource leaks."""
    yield
    plt.close("all")


@pytest.fixture
def visualizer():
    return TrainingVisualizer()


@pytest.fixture
def mock_df():
    return pd.DataFrame(
        {
            "step": range(100),
            "reward": np.random.randn(100),
            "equity": np.linspace(1000, 2000, 100),
            "drawdown": np.random.uniform(0, 0.1, 100),
        }
    )


@patch("matplotlib.pyplot.show")
@patch("matplotlib.figure.Figure.savefig")
def test_plot_training_curves(mock_save, mock_show, visualizer):
    rewards = [1.0, 2.0, 3.0] * 10
    lengths = [100, 200, 300] * 10

    fig = visualizer.plot_training_curves(
        rewards, lengths, show=False, save_path="test.png"
    )
    assert fig is not None
    assert mock_save.called


@patch("matplotlib.pyplot.show")
def test_plot_performance_metrics(mock_show, visualizer):
    metrics = {"sharpe": [1.0, 1.1, 1.2], "drawdown": [0.1, 0.05, 0.02]}
    fig = visualizer.plot_performance_metrics(metrics, show=False)
    assert fig is not None


@patch("matplotlib.pyplot.show")
def test_plot_trading_behavior(mock_show, visualizer):
    prices = np.array([100, 101, 102])
    positions = np.array([0, 1, 1])
    equity = np.array([1000, 1000, 1010])
    trades = [{"step": 1, "side": "buy", "price": 101}]

    fig = visualizer.plot_trading_behavior(
        prices, positions, equity, trades, show=False
    )
    assert fig is not None


@patch("matplotlib.pyplot.show")
def test_plot_sharpe_analysis(mock_show, visualizer):
    returns = np.random.normal(0.001, 0.01, 100)
    fig = visualizer.plot_sharpe_analysis(returns, show=False)
    assert fig is not None


@patch("matplotlib.pyplot.show")
def test_plot_loss_curves(mock_show, visualizer):
    loss = [0.1, 0.05, 0.01]
    fig = visualizer.plot_loss_curves(loss, loss, loss, show=False)
    assert fig is not None


@patch("matplotlib.pyplot.show")
def test_plot_correlation_matrix(mock_show, visualizer):
    df = pd.DataFrame(np.random.randn(10, 3), columns=["a", "b", "c"])
    fig = visualizer.plot_correlation_matrix(df, show=False)
    assert fig is not None


@patch("matplotlib.pyplot.show")
def test_create_training_dashboard(mock_show, visualizer):
    rewards = [1.0] * 10
    lengths = [100] * 10
    prices = np.array([100.0] * 10)
    positions = np.array([1.0] * 10)
    equity = np.array([1000.0] * 10)
    returns = np.array([0.01] * 10)

    fig = visualizer.create_training_dashboard(
        rewards, lengths, prices, positions, equity, returns, show=False
    )
    assert fig is not None
