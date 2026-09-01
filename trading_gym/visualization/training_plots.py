"""
Training Visualization Tools
=============================

Comprehensive visualization tools for analyzing and presenting training results,
including:
- Episode reward curves
- Performance metrics over time
- Trading behavior analysis
- Portfolio value evolution
- Sharpe ratio and risk metrics
- Loss curves for model training
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.figure import Figure

logger = logging.getLogger(__name__)

# Set style
sns.set_style("whitegrid")
plt.rcParams["figure.figsize"] = (12, 8)
plt.rcParams["font.size"] = 10


class TrainingVisualizer:
    """
    Visualizer for training results and performance metrics.

    This class provides comprehensive visualization capabilities for analyzing
    reinforcement learning training runs, including reward curves, metrics,
    and trading behavior.

    Example:
        >>> visualizer = TrainingVisualizer()
        >>> visualizer.plot_training_curves(
        ...     episode_rewards=[100, 150, 200, 250],
        ...     episode_lengths=[1000, 1000, 1000, 1000],
        ...     save_path="training_curves.png"
        ... )
        >>> visualizer.plot_performance_metrics(
        ...     metrics_df=metrics_df,
        ...     save_path="metrics.png"
        ... )
    """

    def __init__(self, style: str = "whitegrid", figsize: Tuple[int, int] = (12, 8)):
        """
        Initialize the visualizer.

        Args:
            style: Seaborn style to use
            figsize: Default figure size (width, height)
        """
        self.style = style
        self.figsize = figsize
        sns.set_style(style)
        logger.info("Initialized TrainingVisualizer with style=%s", style)

    def plot_training_curves(
        self,
        episode_rewards: List[float],
        episode_lengths: List[int],
        moving_avg_window: int = 100,
        save_path: Optional[Union[str, Path]] = None,
        show: bool = True,
    ) -> Figure:
        """
        Plot training reward curves with moving average.

        Args:
            episode_rewards: List of episode rewards
            episode_lengths: List of episode lengths
            moving_avg_window: Window size for moving average
            save_path: Optional path to save the figure
            show: Whether to display the plot

        Returns:
            Matplotlib Figure object
        """
        logger.info("Plotting training curves for %d episodes", len(episode_rewards))

        fig, axes = plt.subplots(2, 1, figsize=self.figsize, sharex=True)

        # Plot rewards
        axes[0].plot(episode_rewards, alpha=0.3, label="Episode Reward")

        if len(episode_rewards) >= moving_avg_window:
            moving_avg = (
                pd.Series(episode_rewards).rolling(window=moving_avg_window).mean()
            )
            axes[0].plot(
                moving_avg,
                linewidth=2,
                label=f"{moving_avg_window}-Episode Moving Avg",
            )

        axes[0].set_ylabel("Reward")
        axes[0].set_title("Training Progress: Episode Rewards")
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # Plot episode lengths
        axes[1].plot(episode_lengths, alpha=0.5, label="Episode Length")

        if len(episode_lengths) >= moving_avg_window:
            length_avg = (
                pd.Series(episode_lengths).rolling(window=moving_avg_window).mean()
            )
            axes[1].plot(
                length_avg,
                linewidth=2,
                label=f"{moving_avg_window}-Episode Moving Avg",
            )

        axes[1].set_xlabel("Episode")
        axes[1].set_ylabel("Length (steps)")
        axes[1].set_title("Episode Lengths")
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            logger.info("Saved training curves to %s", save_path)

        if show:
            plt.show()
        else:
            plt.close(fig)

        return fig

    def plot_performance_metrics(
        self,
        metrics: Dict[str, List[float]],
        save_path: Optional[Union[str, Path]] = None,
        show: bool = True,
    ) -> Figure:
        """
        Plot multiple performance metrics over time.

        Args:
            metrics: Dictionary mapping metric names to value lists
            save_path: Optional path to save the figure
            show: Whether to display the plot

        Returns:
            Matplotlib Figure object
        """
        logger.info("Plotting performance metrics: %s", list(metrics.keys()))

        n_metrics = len(metrics)
        fig, axes = plt.subplots(n_metrics, 1, figsize=(self.figsize[0], n_metrics * 3))

        if n_metrics == 1:
            axes = [axes]

        for ax, (metric_name, values) in zip(axes, metrics.items()):
            ax.plot(values, linewidth=2)
            ax.set_ylabel(metric_name)
            ax.set_title(f"{metric_name} Over Time")
            ax.grid(True, alpha=0.3)

        axes[-1].set_xlabel("Step")
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            logger.info("Saved performance metrics to %s", save_path)

        if show:
            plt.show()
        else:
            plt.close(fig)

        return fig

    def plot_trading_behavior(
        self,
        prices: np.ndarray,
        positions: np.ndarray,
        equity: np.ndarray,
        trades: Optional[List[Dict[str, Any]]] = None,
        save_path: Optional[Union[str, Path]] = None,
        show: bool = True,
    ) -> Figure:
        """
        Visualize trading behavior including prices, positions, and equity.

        Args:
            prices: Array of asset prices
            positions: Array of position sizes
            equity: Array of account equity values
            trades: Optional list of trade dictionaries with 'step', 'side', 'price'
            save_path: Optional path to save the figure
            show: Whether to display the plot

        Returns:
            Matplotlib Figure object
        """
        logger.info("Plotting trading behavior for %d steps", len(prices))

        fig, axes = plt.subplots(3, 1, figsize=self.figsize, sharex=True)

        # Plot prices with buy/sell markers
        axes[0].plot(prices, label="Price", linewidth=2)

        if trades:
            buys = [t for t in trades if t.get("side") == "buy"]
            sells = [t for t in trades if t.get("side") == "sell"]

            if buys:
                buy_steps = [t["step"] for t in buys]
                buy_prices = [t["price"] for t in buys]
                axes[0].scatter(
                    buy_steps,
                    buy_prices,
                    color="green",
                    marker="^",
                    s=100,
                    label="Buy",
                    zorder=5,
                )

            if sells:
                sell_steps = [t["step"] for t in sells]
                sell_prices = [t["price"] for t in sells]
                axes[0].scatter(
                    sell_steps,
                    sell_prices,
                    color="red",
                    marker="v",
                    s=100,
                    label="Sell",
                    zorder=5,
                )

        axes[0].set_ylabel("Price")
        axes[0].set_title("Asset Price with Trades")
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        # Plot positions
        axes[1].fill_between(
            range(len(positions)),
            positions,
            0,
            where=(positions > 0),
            color="green",
            alpha=0.3,
            label="Long",
        )
        axes[1].fill_between(
            range(len(positions)),
            positions,
            0,
            where=(positions < 0),
            color="red",
            alpha=0.3,
            label="Short",
        )
        axes[1].plot(positions, linewidth=1, color="black", alpha=0.5)
        axes[1].axhline(y=0, color="black", linestyle="--", linewidth=1)
        axes[1].set_ylabel("Position")
        axes[1].set_title("Position Over Time")
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        # Plot equity
        axes[2].plot(equity, linewidth=2, color="blue", label="Equity")
        axes[2].axhline(
            y=equity[0],
            color="gray",
            linestyle="--",
            linewidth=1,
            label="Initial Equity",
        )
        axes[2].set_xlabel("Step")
        axes[2].set_ylabel("Equity")
        axes[2].set_title("Portfolio Equity")
        axes[2].legend()
        axes[2].grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            logger.info("Saved trading behavior plot to %s", save_path)

        if show:
            plt.show()
        else:
            plt.close(fig)

        return fig

    def plot_sharpe_analysis(
        self,
        returns: np.ndarray,
        periods_per_year: int = 252,
        save_path: Optional[Union[str, Path]] = None,
        show: bool = True,
    ) -> Figure:
        """
        Analyze and visualize Sharpe ratio and return distribution.

        Args:
            returns: Array of returns
            periods_per_year: Number of periods per year (e.g., 252 for daily)
            save_path: Optional path to save the figure
            show: Whether to display the plot

        Returns:
            Matplotlib Figure object
        """
        logger.info("Plotting Sharpe analysis for %d returns", len(returns))

        fig, axes = plt.subplots(2, 2, figsize=self.figsize)

        # Cumulative returns
        cum_returns = (1 + returns).cumprod()
        axes[0, 0].plot(cum_returns, linewidth=2)
        axes[0, 0].set_title("Cumulative Returns")
        axes[0, 0].set_ylabel("Cumulative Return")
        axes[0, 0].grid(True, alpha=0.3)

        # Rolling Sharpe ratio
        window = min(60, len(returns) // 4)
        if window > 10:
            rolling_mean = pd.Series(returns).rolling(window).mean()
            rolling_std = pd.Series(returns).rolling(window).std()
            rolling_sharpe = (rolling_mean / rolling_std) * np.sqrt(periods_per_year)

            axes[0, 1].plot(rolling_sharpe, linewidth=2)
            axes[0, 1].axhline(y=0, color="black", linestyle="--", linewidth=1)
            axes[0, 1].set_title(f"Rolling Sharpe Ratio ({window} periods)")
            axes[0, 1].set_ylabel("Sharpe Ratio")
            axes[0, 1].grid(True, alpha=0.3)

        # Return distribution
        axes[1, 0].hist(returns, bins=50, alpha=0.7, edgecolor="black")
        axes[1, 0].axvline(x=0, color="red", linestyle="--", linewidth=2)
        axes[1, 0].set_title("Return Distribution")
        axes[1, 0].set_xlabel("Return")
        axes[1, 0].set_ylabel("Frequency")

        # Q-Q plot
        from scipy import stats

        stats.probplot(returns, dist="norm", plot=axes[1, 1])
        axes[1, 1].set_title("Q-Q Plot (Normality Check)")

        # Calculate and display statistics
        sharpe = (
            (np.mean(returns) / np.std(returns)) * np.sqrt(periods_per_year)
            if np.std(returns) > 0
            else 0
        )
        stats_text = f"Sharpe Ratio: {sharpe:.3f}\n"
        stats_text += f"Mean Return: {np.mean(returns):.4f}\n"
        stats_text += f"Std Dev: {np.std(returns):.4f}\n"
        stats_text += f"Skewness: {pd.Series(returns).skew():.3f}\n"
        stats_text += f"Kurtosis: {pd.Series(returns).kurtosis():.3f}"

        fig.text(
            0.02,
            0.98,
            stats_text,
            transform=fig.transFigure,
            fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
        )

        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            logger.info("Saved Sharpe analysis to %s", save_path)

        if show:
            plt.show()
        else:
            plt.close(fig)

        return fig

    def plot_loss_curves(
        self,
        policy_loss: List[float],
        value_loss: List[float],
        entropy: Optional[List[float]] = None,
        save_path: Optional[Union[str, Path]] = None,
        show: bool = True,
    ) -> Figure:
        """
        Plot training loss curves for policy and value networks.

        Args:
            policy_loss: List of policy loss values
            value_loss: List of value loss values
            entropy: Optional list of entropy values
            save_path: Optional path to save the figure
            show: Whether to display the plot

        Returns:
            Matplotlib Figure object
        """
        logger.info("Plotting loss curves")

        n_plots = 3 if entropy else 2
        fig, axes = plt.subplots(n_plots, 1, figsize=(self.figsize[0], n_plots * 3))

        if n_plots == 2:
            axes = [axes[0], axes[1]]
        else:
            axes = [axes[0], axes[1], axes[2]]

        # Policy loss
        axes[0].plot(policy_loss, linewidth=2, color="blue")
        axes[0].set_ylabel("Policy Loss")
        axes[0].set_title("Policy Network Loss")
        axes[0].grid(True, alpha=0.3)

        # Value loss
        axes[1].plot(value_loss, linewidth=2, color="green")
        axes[1].set_ylabel("Value Loss")
        axes[1].set_title("Value Network Loss")
        axes[1].grid(True, alpha=0.3)

        # Entropy (if provided)
        if entropy:
            axes[2].plot(entropy, linewidth=2, color="red")
            axes[2].set_ylabel("Entropy")
            axes[2].set_title("Policy Entropy")
            axes[2].grid(True, alpha=0.3)

        axes[-1].set_xlabel("Update Step")
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            logger.info("Saved loss curves to %s", save_path)

        if show:
            plt.show()
        else:
            plt.close(fig)

        return fig

    def plot_correlation_matrix(
        self,
        features: pd.DataFrame,
        save_path: Optional[Union[str, Path]] = None,
        show: bool = True,
    ) -> Figure:
        """
        Plot correlation matrix heatmap for features.

        Args:
            features: DataFrame of features
            save_path: Optional path to save the figure
            show: Whether to display the plot

        Returns:
            Matplotlib Figure object
        """
        logger.info(
            "Plotting correlation matrix for %d features", len(features.columns)
        )

        fig, ax = plt.subplots(figsize=self.figsize)

        corr_matrix = features.corr()
        sns.heatmap(
            corr_matrix,
            annot=True,
            fmt=".2f",
            cmap="coolwarm",
            center=0,
            ax=ax,
            square=True,
            linewidths=0.5,
        )

        ax.set_title("Feature Correlation Matrix")
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            logger.info("Saved correlation matrix to %s", save_path)

        if show:
            plt.show()
        else:
            plt.close(fig)

        return fig

    def create_training_dashboard(
        self,
        episode_rewards: List[float],
        episode_lengths: List[int],
        prices: np.ndarray,
        positions: np.ndarray,
        equity: np.ndarray,
        returns: np.ndarray,
        save_path: Optional[Union[str, Path]] = None,
        show: bool = True,
    ) -> Figure:
        """
        Create a comprehensive training dashboard with multiple subplots.

        Args:
            episode_rewards: List of episode rewards
            episode_lengths: List of episode lengths
            prices: Array of asset prices
            positions: Array of position sizes
            equity: Array of account equity values
            returns: Array of returns
            save_path: Optional path to save the figure
            show: Whether to display the plot

        Returns:
            Matplotlib Figure object
        """
        logger.info("Creating comprehensive training dashboard")

        fig = plt.figure(figsize=(16, 12))
        gs = fig.add_gridspec(4, 2, hspace=0.3, wspace=0.3)

        # Episode rewards
        ax1 = fig.add_subplot(gs[0, :])
        ax1.plot(episode_rewards, alpha=0.3, label="Episode Reward")
        if len(episode_rewards) >= 100:
            moving_avg = pd.Series(episode_rewards).rolling(window=100).mean()
            ax1.plot(moving_avg, linewidth=2, label="100-Episode Moving Avg")
        ax1.set_ylabel("Reward")
        ax1.set_title("Training Progress: Episode Rewards")
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Prices and positions
        ax2 = fig.add_subplot(gs[1, 0])
        ax2.plot(prices, linewidth=2)
        ax2.set_ylabel("Price")
        ax2.set_title("Asset Price")
        ax2.grid(True, alpha=0.3)

        ax3 = fig.add_subplot(gs[1, 1])
        ax3.plot(positions, linewidth=1)
        ax3.axhline(y=0, color="black", linestyle="--", linewidth=1)
        ax3.set_ylabel("Position")
        ax3.set_title("Position Over Time")
        ax3.grid(True, alpha=0.3)

        # Equity and cumulative returns
        ax4 = fig.add_subplot(gs[2, 0])
        ax4.plot(equity, linewidth=2, color="blue")
        ax4.axhline(y=equity[0], color="gray", linestyle="--", linewidth=1)
        ax4.set_ylabel("Equity")
        ax4.set_title("Portfolio Equity")
        ax4.grid(True, alpha=0.3)

        ax5 = fig.add_subplot(gs[2, 1])
        cum_returns = (1 + returns).cumprod()
        ax5.plot(cum_returns, linewidth=2, color="green")
        ax5.set_ylabel("Cumulative Return")
        ax5.set_title("Cumulative Returns")
        ax5.grid(True, alpha=0.3)

        # Return distribution
        ax6 = fig.add_subplot(gs[3, 0])
        ax6.hist(returns, bins=50, alpha=0.7, edgecolor="black")
        ax6.axvline(x=0, color="red", linestyle="--", linewidth=2)
        ax6.set_xlabel("Return")
        ax6.set_ylabel("Frequency")
        ax6.set_title("Return Distribution")

        # Episode lengths
        ax7 = fig.add_subplot(gs[3, 1])
        ax7.plot(episode_lengths, alpha=0.5)
        ax7.set_xlabel("Episode")
        ax7.set_ylabel("Length (steps)")
        ax7.set_title("Episode Lengths")
        ax7.grid(True, alpha=0.3)

        # Calculate and display overall statistics
        sharpe = (
            (np.mean(returns) / np.std(returns)) * np.sqrt(252)
            if np.std(returns) > 0
            else 0
        )
        total_return = (equity[-1] / equity[0] - 1) * 100
        max_drawdown = (
            np.min(
                (equity - np.maximum.accumulate(equity)) / np.maximum.accumulate(equity)
            )
            * 100
        )

        stats_text = "Performance Summary\n"
        stats_text += f"{'='*30}\n"
        stats_text += f"Total Return: {total_return:.2f}%\n"
        stats_text += f"Sharpe Ratio: {sharpe:.3f}\n"
        stats_text += f"Max Drawdown: {max_drawdown:.2f}%\n"
        stats_text += f"Final Equity: {equity[-1]:.2f}\n"
        stats_text += f"Mean Episode Reward: {np.mean(episode_rewards):.2f}\n"
        stats_text += f"Total Episodes: {len(episode_rewards)}"

        fig.text(
            0.99,
            0.01,
            stats_text,
            transform=fig.transFigure,
            fontsize=10,
            verticalalignment="bottom",
            horizontalalignment="right",
            bbox=dict(boxstyle="round", facecolor="lightblue", alpha=0.7),
        )

        if save_path:
            fig.savefig(save_path, dpi=300, bbox_inches="tight")
            logger.info("Saved training dashboard to %s", save_path)

        if show:
            plt.show()
        else:
            plt.close(fig)

        return fig
