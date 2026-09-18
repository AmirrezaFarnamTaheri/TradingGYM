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
from stable_baselines3.common.env_checker import check_env
from trading_gym.common.config import load_app_config
from trading_gym.common.security import (
    validate_regular_file,
    verified_regular_file_snapshot,
)
import trading_gym.pipelines.environment
from trading_gym.pipelines.environment import create_environment
from trading_gym.data.data_loader import load_data_from_lake
from tqdm import trange
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
            with temp_path.open("rb") as handle:
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
    """Calculate the maximum drawdown."""
    peak_index = 0
    max_drawdown = 0.0
    drawdown_start_index = 0
    for i, equity in enumerate(equity_curve):
        if equity > equity_curve[peak_index]:
            peak_index = i
        drawdown = (equity_curve[peak_index] - equity) / equity_curve[peak_index]
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            drawdown_start_index = peak_index
    dd_end_idx = int(np.argmax(np.maximum.accumulate(equity_curve) - equity_curve))
    return (max_drawdown, int(drawdown_start_index), dd_end_idx)


def sortino_ratio(returns: pd.Series, annualization_factor: float = 252 * 288) -> float:
    """Calculates the Sortino ratio."""
    target_return = 0
    downside_diff = returns - target_return
    downside_diff[downside_diff > 0] = 0
    downside_std = np.sqrt((downside_diff**2).mean())
    if downside_std == 0:
        return np.inf
    mean_return = returns.mean()
    return float((mean_return / downside_std) * np.sqrt(annualization_factor))


def calmar_ratio(
    equity_curve: pd.Series, annualization_factor: float = 252 * 288
) -> float:
    """Calculates the Calmar ratio."""
    max_drawdown, _, _ = calculate_max_drawdown(equity_curve.tolist())
    if max_drawdown == 0.0:
        return np.inf

    returns = equity_curve.pct_change().dropna()
    annualized_return = returns.mean() * annualization_factor

    return float(annualized_return / max_drawdown)


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
    annualization_factor: float = 252.0,
):
    """Atomically plot a bounded rolling annualised Sharpe ratio."""
    if not 2 <= window <= 1_000_000:
        raise ValueError("rolling Sharpe window is out of bounds")
    if not np.isfinite(annualization_factor) or annualization_factor <= 0:
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
        cfg.simulator.steps = min(cfg.simulator.steps, available_rows)
        env = create_environment(cfg, all_data)
        check_env(env)

        with verified_regular_file_snapshot(
            model_artifact,
            expected_sha256=model_sha256,
            label="model",
            max_bytes=8 * 1024 * 1024 * 1024,
        ) as snapshot:
            model = PPO.load(snapshot)
        logger.info("Loaded hash-pinned simulation model")

        raw = load_data_from_lake(
            Path(cfg.data.lake_dir), (cfg.env.symbol,), (timeframe,)
        )[cfg.env.symbol][timeframe]
        feature_index = all_data["features"].index[: cfg.simulator.steps]
        df_ohlc = raw.reindex(feature_index)[["open", "high", "low", "close"]]
        if df_ohlc.isna().any().any() or len(df_ohlc) != cfg.simulator.steps:
            raise ValueError("plot market data does not align with simulation inputs")
        logger.info("Loaded %d aligned simulation rows", len(df_ohlc))

        obs, _ = env.reset()
        equity_curve = [cfg.training.initial_equity]
        positions = []
        logger.info(f"Starting simulation for {cfg.simulator.steps} steps...")
        for i in trange(cfg.simulator.steps, desc="Simulating"):
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, done, truncated, info = env.step(action)
            equity_curve.append(info["equity"])
            positions.append(env.unwrapped.position)

            if (i + 1) % 1000 == 0:
                logger.debug(
                    f"Step {i+1}/{cfg.simulator.steps}: "
                    f"Equity={info['equity']:.2f}, Position={env.unwrapped.position}"
                )

            if done or truncated:
                logger.info(
                    f"Episode finished at step {i+1}. Reason: {'done' if done else 'truncated'}"
                )
                obs, _ = env.reset()

        logger.info("Simulation finished.")

        returns = pd.Series(equity_curve).pct_change().dropna()
        annualization_factor = 252 * 288

        logger.info("Calculating performance metrics...")
        sharpe = (
            returns.mean() / returns.std() * np.sqrt(annualization_factor)
            if returns.std() > 0
            else 0
        )
        sortino = sortino_ratio(returns, annualization_factor)
        max_dd, _, _ = calculate_max_drawdown(equity_curve)
        calmar = calmar_ratio(pd.Series(equity_curve), annualization_factor)

        logger.info(f"Sharpe Ratio: {sharpe:.2f}")
        logger.info(f"Sortino Ratio: {sortino:.2f}")
        logger.info(f"Calmar Ratio: {calmar:.2f}")
        logger.info(f"Max Drawdown: {max_dd:.2%}")

        logger.info("Preparing data for plotting.")
        df_plot = df_ohlc.copy()
        df_plot.index.name = "Date"
        df_plot["equity"] = equity_curve[1:]

        positions_s = pd.Series(positions, index=df_plot.index)
        trades = positions_s.diff().fillna(0)
        buy_signals = np.where(trades > 0, df_plot["low"] * 0.98, np.nan)
        sell_signals = np.where(trades < 0, df_plot["high"] * 1.02, np.nan)

        apds = [
            mpf.make_addplot(df_plot["equity"], panel=1, color="b", ylabel="Equity"),
            mpf.make_addplot(
                buy_signals,
                type="scatter",
                marker="^",
                color="g",
                panel=0,
                markersize=50,
            ),
            mpf.make_addplot(
                sell_signals,
                type="scatter",
                marker="v",
                color="r",
                panel=0,
                markersize=50,
            ),
        ]

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
        fig.text(0.1, 0.92, f"Sortino: {sortino:.2f}", fontsize=12)
        fig.text(0.1, 0.89, f"Calmar: {calmar:.2f}", fontsize=12)
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
                equity_curve, output_path.with_name(f"{output_path.stem}_sharpe.png")
            )

    except Exception as exc:
        logger.critical("Simulation failed error_type=%s", type(exc).__name__)
        raise typer.Exit(code=1) from exc
    finally:
        if env:
            env.close()


if __name__ == "__main__":
    app()
