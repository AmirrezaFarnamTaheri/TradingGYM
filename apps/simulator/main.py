import logging
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import typer
from filelock import FileLock
from stable_baselines3 import PPO
from trading_gym.common.config import load_app_config
from trading_gym.common.security import (
    validate_regular_file,
    verified_regular_file_snapshot,
)
import trading_gym.pipelines.environment
from trading_gym.pipelines.environment import create_environment
from trading_gym.pipelines.model_bundle import read_model_bundle
from trading_gym.common.timeframes import periods_per_year, timeframe_seconds
from trading_gym.pipelines.metrics import (
    maximum_drawdown,
    performance_metrics,
    sortino_ratio as periodic_sortino,
    validate_regular_decisions,
)
from trading_gym.pipelines.replay import replay_episode
import mplfinance as mpf

app = typer.Typer()
logger = logging.getLogger(__name__)


def _atomic_save_figure(figure, output_path: Path) -> Path:
    """Publish one PNG through a locked, fsync-backed atomic replacement."""

    output = Path(output_path).expanduser()
    if output.suffix.lower() != ".png":
        raise ValueError("simulation output must use a .png suffix")
    if output.exists() and output.is_symlink():
        raise ValueError("simulation output must not be a symbolic link")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.parent.is_symlink():
        raise ValueError("simulation output directory must not be a symbolic link")
    with FileLock(str(output) + ".lock", timeout=15):
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
        )
        os.close(descriptor)
        temp_path = Path(temp_name)
        try:
            figure.savefig(temp_path, format="png")
            size = temp_path.stat().st_size
            if size <= 0 or size > 256 * 1024 * 1024:
                raise ValueError("simulation plot is empty or exceeds its size limit")
            with temp_path.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(temp_path, output)
            try:
                output.chmod(0o600)
            except OSError:
                pass
            flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
            try:
                directory = os.open(output.parent, flags)
            except OSError:
                directory = None
            if directory is not None:
                try:
                    os.fsync(directory)
                except OSError:
                    pass
                finally:
                    os.close(directory)
            return output
        finally:
            if temp_path.exists():
                temp_path.unlink()


def calculate_max_drawdown(equity_curve: list[float]) -> tuple[float, int, int]:
    """Percentage drawdown and its matching peak/trough indices."""
    return maximum_drawdown(equity_curve)


def sortino_ratio(returns: pd.Series, annualization_factor: float) -> float | None:
    return periodic_sortino(returns.to_numpy(), annualization_factor)


def calmar_ratio(equity_curve: pd.Series, annualization_factor: float) -> float | None:
    return performance_metrics(
        equity_curve.to_numpy(), periods_per_year=annualization_factor
    ).calmar


def _metric_label(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}"


def plot_returns_distribution(equity_curve: list[float], output_path: Path):
    """Atomically plot the distribution of returns."""
    returns = pd.Series(equity_curve).pct_change().dropna()
    figure, axis = plt.subplots(figsize=(12, 6))
    returns.hist(bins=50, ax=axis)
    axis.set_title("Returns Distribution")
    axis.set_xlabel("Return")
    axis.set_ylabel("Frequency")
    axis.grid(True)
    try:
        _atomic_save_figure(figure, output_path)
    finally:
        plt.close(figure)
    logger.info("Returns distribution plot published")


def plot_rolling_sharpe_ratio(
    equity_curve: list[float],
    output_path: Path,
    window: int = 252,
    annualization_factor: float | None = None,
):
    """Atomically plot a bounded rolling annualised Sharpe ratio."""
    if not 2 <= window <= 1_000_000:
        raise ValueError("rolling Sharpe window is out of bounds")
    if (
        annualization_factor is None
        or not np.isfinite(annualization_factor)
        or annualization_factor <= 0
    ):
        raise ValueError("annualization_factor must be finite and positive")
    returns = pd.Series(equity_curve).pct_change().dropna()
    rolling_mean = returns.rolling(window).mean()
    rolling_std = returns.rolling(window).std()
    rolling_sharpe_ratio = (rolling_mean / rolling_std) * np.sqrt(annualization_factor)
    figure, axis = plt.subplots(figsize=(12, 6))
    rolling_sharpe_ratio.plot(ax=axis)
    axis.set_title("Rolling Sharpe Ratio (Annualized)")
    axis.set_xlabel("Step")
    axis.set_ylabel("Sharpe Ratio")
    axis.grid(True)
    try:
        _atomic_save_figure(figure, output_path)
    finally:
        plt.close(figure)
    logger.info("Rolling Sharpe ratio plot published")


@app.command()
def run(
    config_path: str = typer.Option("config/app.yaml", "--config", "-c"),
    model_path: Path = typer.Option(
        ..., "--model-path", "-m", help="Path to the trained model"
    ),
    model_sha256: str = typer.Option(
        ..., "--model-sha256", help="Expected SHA-256 of the admitted model artifact"
    ),
    output_path: Path = typer.Option(
        "backtest.png", "--output-path", "-o", help="Path to save the backtest plot"
    ),
    timeframe: str = typer.Option(
        "5m", "--timeframe", "-t", help="The timeframe to use for the simulation"
    ),
    start_date: Optional[date] = typer.Option(None, "--start-date"),
    end_date: Optional[date] = typer.Option(None, "--end-date"),
    plot_returns: bool = typer.Option(
        False, "--plot-returns", help="Plot the returns distribution"
    ),
    plot_sharpe: bool = typer.Option(
        False, "--plot-sharpe", help="Plot the rolling Sharpe ratio"
    ),
):
    """Run a hash-pinned, date-bounded backtest and publish plots atomically."""
    try:
        config = validate_regular_file(
            config_path, label="configuration", max_bytes=2 * 1024 * 1024
        )
        model_artifact = validate_regular_file(
            model_path, label="model", max_bytes=8 * 1024 * 1024 * 1024
        )
        output_path = Path(output_path).expanduser()
        if (start_date is None) != (end_date is None):
            raise ValueError("start-date and end-date must be supplied together")
        if start_date is not None and end_date is not None:
            days = (end_date - start_date).days
            if days <= 0 or days > 3660:
                raise ValueError(
                    "simulation date window must be between 1 and 3660 days"
                )
            start_at = pd.Timestamp(start_date, tz="UTC")
            end_at = pd.Timestamp(end_date + timedelta(days=1), tz="UTC")
        else:
            start_at = end_at = None
        cfg = load_app_config(config)
    except Exception as exc:
        logger.error(
            "Simulator input validation failed error_type=%s", type(exc).__name__
        )
        raise typer.Exit(code=1) from exc

    env = None
    try:
        logger.info("Creating date-bounded simulation environment")
        all_data = trading_gym.pipelines.environment.load_environment_inputs(
            cfg,
            timeframe,
            start_at=start_at,
            end_at=end_at,
        )
        available_rows = len(all_data["features"])
        if available_rows < 2:
            raise ValueError("simulation window contains insufficient rows")
        cfg.simulator.steps = min(cfg.simulator.steps, available_rows - 1)

        with verified_regular_file_snapshot(
            model_artifact,
            expected_sha256=model_sha256,
            label="model",
            max_bytes=8 * 1024 * 1024 * 1024,
        ) as snapshot:
            bundle = read_model_bundle(snapshot, expected_model_sha256=model_sha256)
            bundle.validate_config(cfg)
            bundle.validate_features(all_data["features"])
            bundle.validate_runtime()
            model = PPO.load(snapshot)
        logger.info("Loaded hash-pinned simulation model and frozen preprocessing")
        env = create_environment(cfg, all_data, training_stats=bundle.training_stats)

        trace = replay_episode(model, env, seed=cfg.seed, max_steps=cfg.simulator.steps)
        equity_curve = list(trace.equity)
        positions = list(trace.positions)
        consumed = list(trace.decision_indices)
        valued = list(trace.valuation_indices)
        valuation_rows = [consumed[0], *valued]
        valuation_times = (
            all_data["features"].index[valuation_rows].asi8.astype(np.float64) / 1e9
        )
        validate_regular_decisions(valuation_times, timeframe_seconds(timeframe))
        df_ohlc = all_data["ohlc"].iloc[valuation_rows].copy()
        if len(df_ohlc) != len(equity_curve) or df_ohlc.isna().any().any():
            raise ValueError("plot market data does not align with settled valuations")
        annualization_factor = periods_per_year(timeframe, calendar_days=365.0)
        metrics = performance_metrics(
            equity_curve, periods_per_year=annualization_factor
        )
        sharpe, sortino, calmar, max_dd = (
            metrics.sharpe,
            metrics.sortino,
            metrics.calmar,
            metrics.max_drawdown,
        )
        logger.info("Simulation ended after %d continuous decisions", len(consumed))
        logger.info(
            "Sharpe=%s Sortino=%s Calmar=%s MaxDrawdown=%.4f periods/year=%.4f",
            _metric_label(sharpe),
            _metric_label(sortino),
            _metric_label(calmar),
            max_dd,
            annualization_factor,
        )

        logger.info("Preparing data for plotting.")
        df_plot = df_ohlc.copy()
        df_plot.index.name = "Date"
        df_plot["equity"] = equity_curve

        # Position changes shown at their settled valuation, including any
        # intrabar stop-out; these markers are not fabricated venue fill times.
        trades = pd.Series(
            np.r_[0.0, np.diff(np.r_[0.0, positions])], index=df_plot.index
        )
        buy_signals = np.where(trades > 0, df_plot["low"] * 0.98, np.nan)
        sell_signals = np.where(trades < 0, df_plot["high"] * 1.02, np.nan)

        apds = [
            mpf.make_addplot(df_plot["equity"], panel=1, color="b", ylabel="Equity")
        ]
        for signal, marker, color in (
            (buy_signals, "^", "g"),
            (sell_signals, "v", "r"),
        ):
            if np.isfinite(signal).any():
                apds.append(
                    mpf.make_addplot(
                        signal,
                        type="scatter",
                        marker=marker,
                        color=color,
                        panel=0,
                        markersize=50,
                    )
                )

        fig, _ = mpf.plot(
            df_plot,
            type="candle",
            style="charles",
            title=f"{cfg.env.symbol} Backtest",
            ylabel="Price",
            addplot=apds,
            panel_ratios=(3, 1),
            figscale=1.5,
            returnfig=True,
        )

        fig.text(0.1, 0.95, f"Sharpe: {sharpe:.2f}", fontsize=12)
        fig.text(0.1, 0.92, f"Sortino: {_metric_label(sortino)}", fontsize=12)
        fig.text(0.1, 0.89, f"Calmar: {_metric_label(calmar)}", fontsize=12)
        fig.text(0.1, 0.86, f"Max Drawdown: {max_dd:.2%}", fontsize=12)

        try:
            _atomic_save_figure(fig, output_path)
        finally:
            plt.close(fig)
        logger.info("Backtest plot published")

        if plot_returns:
            plot_returns_distribution(
                equity_curve, output_path.with_name(f"{output_path.stem}_returns.png")
            )

        if plot_sharpe:
            plot_rolling_sharpe_ratio(
                equity_curve,
                output_path.with_name(f"{output_path.stem}_sharpe.png"),
                annualization_factor=annualization_factor,
            )

    except Exception as exc:
        logger.critical("Simulation failed error_type=%s", type(exc).__name__)
        raise typer.Exit(code=1) from exc
    finally:
        if env:
            env.close()


if __name__ == "__main__":
    app()
