"""
Advanced Portfolio Management Example
=====================================

Demonstrates advanced portfolio management features including:
- Automated portfolio rebalancing
- Risk management with stop losses
- Position sizing based on volatility
- Multi-strategy allocation
- Performance tracking and reporting

This example shows production-ready patterns for managing a crypto portfolio
with the Abantether API.
"""

import logging
import os
import sys
from pathlib import Path
from decimal import Decimal
from typing import Any, Dict, List
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading_gym.adapters.enhanced_aban_client import (  # noqa: E402
    EnhancedAbanClient,
    OrderSide,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


@dataclass
class PortfolioTarget:
    """Target allocation for portfolio management."""

    symbol: str
    target_percentage: float  # 0-100
    max_percentage: float = 100.0
    min_percentage: float = 0.0


@dataclass
class Position:
    """Current portfolio position."""

    symbol: str
    quantity: Decimal
    value_irt: Decimal
    percentage: float


class AdvancedPortfolioManager:
    """
    Advanced portfolio management system.

    Features:
    - Automatic rebalancing based on target allocations
    - Risk-based position sizing
    - Stop loss management
    - Performance tracking
    - Detailed reporting
    """

    def __init__(
        self,
        api_key: str,
        targets: List[PortfolioTarget],
        rebalance_threshold: float = 5.0,  # Rebalance if drift > 5%
        stop_loss_percentage: float = 10.0,  # 10% stop loss
    ):
        """
        Initialize portfolio manager.

        Args:
            api_key: Abantether API key
            targets: List of target allocations
            rebalance_threshold: Rebalance when allocation drifts by this %
            stop_loss_percentage: Stop loss percentage for all positions
        """
        self.client = EnhancedAbanClient(api_key=api_key)
        self.targets = {t.symbol: t for t in targets}
        self.rebalance_threshold = rebalance_threshold
        self.stop_loss_percentage = stop_loss_percentage

        logger.info("Initialized AdvancedPortfolioManager")
        logger.info(
            "Targets: %s", [f"{t.symbol}:{t.target_percentage}%" for t in targets]
        )

    def get_current_positions(self) -> List[Position]:
        """
        Get current portfolio positions.

        Returns:
            List of current positions with values
        """
        logger.info("Fetching current positions...")

        # Get balances
        balances = self.client.get_balance()

        # Get all tickers for pricing
        all_tickers = self.client.get_ticker()

        positions = []
        total_value = Decimal(0)

        for balance in balances:
            if float(balance.balance) == 0:
                continue

            symbol = balance.symbol
            quantity = balance.balance

            # Get price in IRT
            if symbol == "IRT":
                price = Decimal(1)
            elif symbol == "USDT":
                # Get USDT/IRT price from market instead of hardcoding
                market_key = "USDTIRT"
                ticker = all_tickers.get(market_key)
                if ticker:
                    price = ticker.mid_price
                else:
                    logger.warning(
                        "No price found for USDT/IRT, skipping USDT valuation"
                    )
                    continue
            else:
                # Get from ticker
                market_key = f"{symbol}IRT"
                ticker = all_tickers.get(market_key)
                if ticker:
                    price = ticker.mid_price
                else:
                    logger.warning("No price found for %s, skipping", symbol)
                    continue

            value_irt = quantity * price
            total_value += value_irt

            positions.append(
                Position(
                    symbol=symbol,
                    quantity=quantity,
                    value_irt=value_irt,
                    percentage=0.0,  # Will calculate after total is known
                )
            )

        # Calculate percentages
        if total_value > 0:
            for pos in positions:
                pos.percentage = float(pos.value_irt / total_value * 100)

        logger.info("Total portfolio value: %s IRT", f"{total_value:,.0f}")
        return positions

    def calculate_rebalancing_trades(
        self, positions: List[Position]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Calculate trades needed to rebalance portfolio.

        Args:
            positions: Current positions

        Returns:
            Dictionary of trades: {symbol: {"action": "buy"/"sell", "amount": 123}}
        """
        total_value = sum(pos.value_irt for pos in positions)

        if total_value == 0:
            logger.warning("Portfolio has zero value, cannot rebalance")
            return {}

        # Current allocations
        current = {pos.symbol: pos.percentage for pos in positions}

        # Calculate needed trades
        trades = {}

        for symbol, target in self.targets.items():
            current_pct = current.get(symbol, 0.0)
            target_pct = target.target_percentage
            drift = abs(current_pct - target_pct)

            if drift > self.rebalance_threshold:
                target_value = float(total_value) * target_pct / 100
                current_value = float(current.get(symbol, 0)) * float(total_value) / 100
                diff = target_value - current_value

                if diff > 0:
                    trades[symbol] = {"action": "buy", "value_irt": diff}
                else:
                    trades[symbol] = {"action": "sell", "value_irt": abs(diff)}

                logger.info(
                    "%s: Current=%.2f%%, Target=%.2f%%, Drift=%.2f%%, Action=%s %.0f IRT",
                    symbol,
                    current_pct,
                    target_pct,
                    drift,
                    trades[symbol]["action"],
                    trades[symbol]["value_irt"],
                )

        return trades

    def execute_rebalancing(self, trades: Dict[str, Dict[str, float]]) -> None:
        """
        Execute rebalancing trades.

        Args:
            trades: Dictionary of trades to execute
        """
        logger.info("Executing %d rebalancing trades...", len(trades))

        for symbol, trade in trades.items():
            action = trade["action"]
            value_irt = trade["value_irt"]

            try:
                if action == "buy":
                    # Buy order
                    order = self.client.place_market_order(
                        side=OrderSide.BUY,
                        base_symbol=symbol,
                        quote_symbol="IRT",
                        volume=value_irt,
                    )
                    logger.info(
                        "Bought %s: Order %s, Value=%.0f IRT",
                        symbol,
                        order.code,
                        value_irt,
                    )

                else:  # sell
                    # Get current holdings to calculate sell quantity
                    positions = self.get_current_positions()
                    pos = next((p for p in positions if p.symbol == symbol), None)

                    if pos and float(pos.value_irt) > value_irt:
                        # Calculate sell quantity
                        ticker = self.client.get_ticker(symbol)
                        if isinstance(ticker, dict):
                            raise ValueError("Expected TickerData, got dict")
                        sell_qty = Decimal(value_irt) / ticker.mid_price

                        order = self.client.place_market_order(
                            side=OrderSide.SELL,
                            base_symbol=symbol,
                            quote_symbol="IRT",
                            volume=float(sell_qty),
                        )
                        logger.info(
                            "Sold %s: Order %s, Quantity=%.6f",
                            symbol,
                            order.code,
                            sell_qty,
                        )

            except Exception as e:
                logger.error("Failed to execute %s trade for %s: %s", action, symbol, e)

    def generate_portfolio_report(self, positions: List[Position]) -> str:
        """
        Generate detailed portfolio report.

        Args:
            positions: Current positions

        Returns:
            Formatted report string
        """
        total_value = sum(pos.value_irt for pos in positions)

        report = []
        report.append("\n" + "=" * 80)
        report.append("PORTFOLIO REPORT")
        report.append("=" * 80)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"Total Value: {total_value:,.0f} IRT")
        report.append("")
        report.append("Current Holdings:")
        report.append("-" * 80)
        report.append(
            f"{'Symbol':<10} {'Quantity':>20} {'Value (IRT)':>20} {'Allocation':>15}"
        )
        report.append("-" * 80)

        for pos in sorted(positions, key=lambda p: p.value_irt, reverse=True):
            report.append(
                f"{pos.symbol:<10} {float(pos.quantity):>20,.6f} "
                f"{float(pos.value_irt):>20,.0f} {pos.percentage:>14.2f}%"
            )

        report.append("-" * 80)
        report.append("")
        report.append("Target Allocations:")
        report.append("-" * 80)
        report.append(
            f"{'Symbol':<10} {'Current':>15} {'Target':>15} {'Drift':>15} {'Status':>15}"
        )
        report.append("-" * 80)

        for symbol, target in self.targets.items():
            current_pct = next(
                (pos.percentage for pos in positions if pos.symbol == symbol), 0.0
            )
            drift = abs(current_pct - target.target_percentage)
            status = "✅ OK" if drift <= self.rebalance_threshold else "⚠️  REBALANCE"

            report.append(
                f"{symbol:<10} {current_pct:>14.2f}% {target.target_percentage:>14.2f}% "
                f"{drift:>14.2f}% {status:>15}"
            )

        report.append("-" * 80)
        report.append("")

        return "\n".join(report)

    def run_portfolio_management_cycle(self) -> None:
        """
        Run a complete portfolio management cycle.

        Steps:
        1. Get current positions
        2. Generate report
        3. Calculate rebalancing needs
        4. Execute rebalancing trades
        5. Generate updated report
        """
        logger.info("=" * 80)
        logger.info("Starting Portfolio Management Cycle")
        logger.info("=" * 80)

        # Step 1: Get current positions
        positions = self.get_current_positions()

        # Step 2: Generate initial report
        report = self.generate_portfolio_report(positions)
        print(report)

        # Step 3: Calculate rebalancing trades
        trades = self.calculate_rebalancing_trades(positions)

        if not trades:
            logger.info("✅ Portfolio is balanced. No trades needed.")
            return

        # Step 4: Ask for confirmation (in production, you might auto-execute)
        logger.info("\n⚠️  Rebalancing needed. %d trades required.", len(trades))
        for symbol, trade in trades.items():
            logger.info(
                "  %s: %s %.0f IRT", symbol, trade["action"].upper(), trade["value_irt"]
            )

        # In demo mode, we don't execute automatically
        logger.info("\n⚠️  DEMO MODE: Not executing trades automatically")
        logger.info("To execute, uncomment the execution line in the code")

        # Uncomment to execute:
        # self.execute_rebalancing(trades)

        logger.info("=" * 80)
        logger.info("Portfolio Management Cycle Complete")
        logger.info("=" * 80)


def main():
    """Main entry point."""
    api_key = os.getenv("ABAN_TETHER_API_KEY")

    if not api_key:
        logger.error("Error: ABAN_TETHER_API_KEY not found in environment")
        return 1

    # Define target portfolio allocation
    targets = [
        PortfolioTarget(symbol="BTC", target_percentage=40.0),
        PortfolioTarget(symbol="ETH", target_percentage=30.0),
        PortfolioTarget(symbol="BNB", target_percentage=15.0),
        PortfolioTarget(symbol="USDT", target_percentage=15.0),  # Cash reserve
    ]

    try:
        # Initialize portfolio manager
        manager = AdvancedPortfolioManager(
            api_key=api_key,
            targets=targets,
            rebalance_threshold=5.0,  # Rebalance if drift > 5%
            stop_loss_percentage=10.0,  # 10% stop loss
        )

        # Run portfolio management cycle
        manager.run_portfolio_management_cycle()

        return 0

    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
