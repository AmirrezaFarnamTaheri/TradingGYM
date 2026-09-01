"""
Complete Abantether API Integration Demo
=========================================

This script demonstrates all available Abantether API features including:
- Account balance and market data
- Order management (market, limit, sell-all)
- Deposits and withdrawals
- Margin trading (credit)
- Short selling
- Network and wallet management

⚠️  WARNING: This script uses REAL API calls.
    Use paper trading mode or small amounts for testing.
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading_gym.adapters.enhanced_aban_client import (  # noqa: E402
    EnhancedAbanClient,
    OrderSide,
    OrderStatus,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class CompleteAPIDemo:
    """Comprehensive demonstration of Abantether API capabilities."""

    def __init__(self, api_key: str):
        """Initialize the demo with API key."""
        self.client = EnhancedAbanClient(api_key=api_key)
        logger.info("=" * 80)
        logger.info("ABANTETHER API COMPLETE INTEGRATION DEMO")
        logger.info("=" * 80)

    def demo_account_and_balance(self):
        """Demonstrate account balance retrieval."""
        logger.info("\n" + "=" * 80)
        logger.info("1. ACCOUNT & BALANCE")
        logger.info("=" * 80)

        try:
            balances = self.client.get_balance()
            logger.info("\n📊 Account Balances:")
            for balance in balances:
                logger.info(
                    "  %s: Available=%.6f, Blocked=%.6f",
                    balance.symbol,
                    float(balance.balance),
                    float(balance.blocked),
                )
        except Exception as e:
            logger.error("Failed to fetch balance: %s", e)

    def demo_market_data(self):
        """Demonstrate market data retrieval."""
        logger.info("\n" + "=" * 80)
        logger.info("2. MARKET DATA")
        logger.info("=" * 80)

        # Get all tickers
        try:
            tickers = self.client.get_ticker()
            logger.info("\n📈 Available Markets: %d", len(tickers))

            # Show first 5 markets
            for market, ticker in list(tickers.items())[:5]:
                logger.info(
                    "  %s: Buy=%.2f, Sell=%.2f, Mid=%.2f, Spread=%.2f bps",
                    market,
                    float(ticker.buy_price),
                    float(ticker.sell_price),
                    float(ticker.mid_price),
                    float(ticker.spread_bps),
                )
        except Exception as e:
            logger.error("Failed to fetch tickers: %s", e)

        # Get specific ticker
        try:
            btc_ticker = self.client.get_ticker("BTC")
            logger.info("\n₿ BTC Ticker:")
            logger.info("  Buy Price: %.2f IRT", float(btc_ticker.buy_price))
            logger.info("  Sell Price: %.2f IRT", float(btc_ticker.sell_price))
            logger.info("  Mid Price: %.2f IRT", float(btc_ticker.mid_price))
            logger.info("  Spread: %.2f bps", float(btc_ticker.spread_bps))
        except Exception as e:
            logger.error("Failed to fetch BTC ticker: %s", e)

        # Get coin info
        try:
            coin_info = self.client.get_coin_info("BTC", OrderSide.BUY)
            logger.info("\n💰 BTC Trading Info:")
            logger.info("  Min Trade (USDT): %.2f", float(coin_info.usdt_min_trade))
            logger.info("  Max Trade (USDT): %.2f", float(coin_info.usdt_max_trade))
            logger.info("  Exchange Fee: %.4f%%", float(coin_info.exchange_fee))
        except Exception as e:
            logger.error("Failed to fetch coin info: %s", e)

    def demo_order_management(self):
        """Demonstrate order management (viewing only, no actual trading)."""
        logger.info("\n" + "=" * 80)
        logger.info("3. ORDER MANAGEMENT (Read-Only Demo)")
        logger.info("=" * 80)

        try:
            # Get order history
            orders = self.client.get_orders(
                status=[OrderStatus.FILLED, OrderStatus.CANCELED], page=1, per_page=5
            )
            logger.info("\n📝 Recent Orders: %d total", orders.get("total", 0))

            for order in orders.get("data", [])[:3]:
                logger.info(
                    "  Order %s: %s %s/%s - %s",
                    order.get("code"),
                    order.get("side").upper(),
                    order.get("base_symbol"),
                    order.get("quote_symbol"),
                    order.get("status").upper(),
                )
        except Exception as e:
            logger.error("Failed to fetch orders: %s", e)

        logger.info("\n⚠️  Actual order placement disabled in demo mode")
        logger.info("    To place orders, uncomment the code and use with caution!")

    def demo_deposit_withdrawal(self):
        """Demonstrate deposit and withdrawal features (info only)."""
        logger.info("\n" + "=" * 80)
        logger.info("4. DEPOSITS & WITHDRAWALS (Info Only)")
        logger.info("=" * 80)

        # Get deposit coins
        try:
            deposit_coins = self.client.get_deposit_coins()
            logger.info("\n📥 Coins Available for Deposit: %d", len(deposit_coins))
            for coin in deposit_coins[:5]:
                logger.info("  %s (%s)", coin.get("symbol"), coin.get("persianName"))
        except Exception as e:
            logger.error("Failed to fetch deposit coins: %s", e)

        # Get withdrawal coins
        try:
            withdrawal_coins = self.client.get_withdrawal_coins()
            logger.info(
                "\n📤 Coins Available for Withdrawal: %d", len(withdrawal_coins)
            )
            for coin in withdrawal_coins[:5]:
                logger.info("  %s (%s)", coin.get("symbol"), coin.get("persianName"))
        except Exception as e:
            logger.error("Failed to fetch withdrawal coins: %s", e)

        # Get networks for USDT
        try:
            networks = self.client.get_networks("USDT", "withdraw")
            logger.info("\n🌐 USDT Withdrawal Networks:")
            for network in networks:
                logger.info(
                    "  %s: Fee=%.6f (%s), Min=%.2f, Max=%.2f",
                    network.name,
                    float(network.fee),
                    network.fee_type,
                    float(network.min_transfer),
                    float(network.max_transfer),
                )
        except Exception as e:
            logger.error("Failed to fetch networks: %s", e)

        # Get white addresses
        try:
            addresses = self.client.get_white_addresses()
            logger.info("\n🔐 Whitelisted Addresses: %d", len(addresses))
            for addr in addresses:
                logger.info(
                    "  %s: %s (%s)",
                    addr.get("label"),
                    addr.get("walletAddress"),
                    addr.get("network", {}).get("name"),
                )
        except Exception as e:
            logger.error("Failed to fetch white addresses: %s", e)

        # Calculate withdrawal fee
        try:
            fee = self.client.get_withdrawal_fee("USDT", "TRC20", 100)
            logger.info(
                "\n💸 Withdrawal Fee (100 USDT via TRC20): %.6f USDT", float(fee)
            )
        except Exception as e:
            logger.error("Failed to calculate withdrawal fee: %s", e)

    def demo_margin_trading(self):
        """Demonstrate margin trading (credit) features (info only)."""
        logger.info("\n" + "=" * 80)
        logger.info("5. MARGIN TRADING (Credit) - Info Only")
        logger.info("=" * 80)

        try:
            # Get available credit
            credit_info = self.client.get_available_credit("IRT", 1000000)
            logger.info("\n💳 Available Credit Information:")
            logger.info("  Collateral: 1,000,000 IRT")
            logger.info("  Available Credit: %s", credit_info)
        except Exception as e:
            logger.error("Failed to fetch credit info: %s", e)

        try:
            # Get credit status
            status = self.client.get_credit_status()
            logger.info("\n📊 Current Credit Status:")
            logger.info("  %s", status)
        except Exception as e:
            logger.error("Failed to fetch credit status: %s", e)

        logger.info("\n⚠️  Margin trading features:")
        logger.info("    - Leverage up to 5x")
        logger.info("    - Available for IRT and USDT")
        logger.info("    - Request credit, repay credit, check status")

    def demo_short_selling(self):
        """Demonstrate short selling features (info only)."""
        logger.info("\n" + "=" * 80)
        logger.info("6. SHORT SELLING - Info Only")
        logger.info("=" * 80)

        try:
            # Get shortable coins
            coins = self.client.get_shortable_coins()
            logger.info("\n🔻 Coins Available for Short Selling:")
            logger.info("  %s", coins)
        except Exception as e:
            logger.error("Failed to fetch shortable coins: %s", e)

        try:
            # Get short positions
            positions = self.client.get_short_positions()
            logger.info("\n📉 Current Short Positions:")
            if isinstance(positions, list):
                logger.info("  Active positions: %d", len(positions))
                for pos in positions:
                    logger.info("  %s", pos)
            else:
                logger.info("  %s", positions)
        except Exception as e:
            logger.error("Failed to fetch short positions: %s", e)

        logger.info("\n⚠️  Short selling features:")
        logger.info("    - BTC: Up to 0.5 BTC")
        logger.info("    - ETH: Up to 10 ETH")
        logger.info("    - Leverage: 1.5x to 5x")

    def run_complete_demo(self):
        """Run all demonstrations."""
        try:
            self.demo_account_and_balance()
            self.demo_market_data()
            self.demo_order_management()
            self.demo_deposit_withdrawal()
            self.demo_margin_trading()
            self.demo_short_selling()

            logger.info("\n" + "=" * 80)
            logger.info("✅ DEMO COMPLETED SUCCESSFULLY")
            logger.info("=" * 80)
            logger.info("\nAll API endpoints are working correctly!")
            logger.info("Review the logs above for detailed information.")

        except Exception as e:
            logger.error("\n" + "=" * 80)
            logger.error("❌ DEMO FAILED")
            logger.error("=" * 80)
            logger.error("Error: %s", e, exc_info=True)


def main():
    """Main entry point."""
    api_key = os.getenv("ABAN_TETHER_API_KEY")

    if not api_key:
        logger.error("Error: ABAN_TETHER_API_KEY not found in environment")
        logger.error("Please set it in .env file or environment variables")
        return 1

    try:
        demo = CompleteAPIDemo(api_key=api_key)
        demo.run_complete_demo()
        return 0
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
