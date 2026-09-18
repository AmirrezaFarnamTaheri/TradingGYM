"""Authoritative tradeable-symbol metadata: decimals, limits, and short/leverage flags.

Backed by ``Leverage_and_Shorting.txt`` for the flags and the venue coin-info
endpoint for the numeric metadata.

    registry = SymbolRegistry(sdk)
    await registry.populate()
    btc_meta = registry.get("BTC")
    leverage_symbols = registry.get_leverage_symbols()
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SymbolMetadata:
    """
    Metadata for a trading symbol.

    Attributes:
        symbol: Symbol name (e.g., "BTC", "ETH")
        leverage_enabled: Whether leverage trading is enabled
        short_enabled: Whether short selling is enabled
        short_cap: Maximum short position size (None if unlimited)
        decimals: Price/quantity decimals
        min_trade_irt: Minimum trade size in IRT
        max_trade_irt: Maximum trade size in IRT
        min_trade_usdt: Minimum trade size in USDT
        max_trade_usdt: Maximum trade size in USDT
        exchange_fee_pct: Exchange fee percentage (e.g., 0.002 = 0.2%)
        last_updated: When metadata was last refreshed
        active: Whether symbol is currently tradeable
    """

    symbol: str
    leverage_enabled: bool
    short_enabled: bool
    short_cap: Optional[float] = None

    # From coin-info endpoint
    decimals: Optional[int] = None
    min_trade_irt: Optional[float] = None
    max_trade_irt: Optional[float] = None
    min_trade_usdt: Optional[float] = None
    max_trade_usdt: Optional[float] = None
    exchange_fee_pct: Optional[float] = None

    # Metadata
    last_updated: Optional[datetime] = None
    active: bool = True


class SymbolRegistry:
    def __init__(self, sdk_client, config_path="config/market_rules.json"):
        """
        Initialize registry.

        Args:
            sdk_client: AbanSDKWrapper or RateLimitedAbanSDK instance for fetching live data
        """
        self.sdk = sdk_client
        self.symbols: Dict[str, SymbolMetadata] = {}
        self._populated = False
        self.config_path = Path(config_path)
        self.leverage_symbols = set()
        self.short_caps = {}

        self._load_rules()
        logger.info("SymbolRegistry initialized")

    def _load_rules(self):
        # 1. Load static rules (caps, allowances) from external file
        if self.config_path.exists():
            with open(self.config_path, "r") as f:
                rules = json.load(f)
                self.leverage_symbols = set(rules.get("leverage_symbols", []))
                self.short_caps = rules.get("short_caps", {})
        else:
            logger.warning("No market_rules.json found. Falling back to defaults.")

    async def populate(self, force_refresh: bool = False) -> None:
        """
        Populate registry from API.

        Fetches all available symbols from the broker API and enriches
        with leverage/short information from hardcoded lists.

        Args:
            force_refresh: Force refresh even if already populated

        Raises:
            Exception: If API calls fail
        """
        if self._populated and not force_refresh:
            logger.info(
                "Registry already populated, skipping (use force_refresh=True to refresh)"
            )
            return

        logger.info("Populating symbol registry from API...")

        try:
            # Get ticker to find all active symbols
            ticker_data = await self.sdk.get_ticker()
            markets = ticker_data.get("data", {}).get("markets", {})
            api_symbols = [
                market.get("symbol")
                for market in markets.values()
                if market.get("symbol")
            ]

            if not markets:
                logger.warning("No markets found in ticker response")
                return

            symbols_added = 0
            symbols_failed = 0

            for symbol in api_symbols:
                # Create metadata with basic info
                metadata = SymbolMetadata(
                    symbol=symbol,
                    leverage_enabled=(symbol in self.leverage_symbols),
                    short_enabled=(symbol in self.short_caps),
                    short_cap=self.short_caps.get(symbol),
                    active=True,  # Assuming active if in ticker
                )

                # Try to fetch detailed info (decimals, limits, fees)
                # This is optional and non-blocking
                try:
                    # Guard: Only call if SDK has the method
                    if hasattr(self.sdk, "get_coin_info"):
                        coin_info = await self.sdk.get_coin_info(symbol, "buy")
                        data = coin_info.get("data", {}) or {}

                        def _to_float(val):
                            try:
                                return float(val)
                            except (TypeError, ValueError):
                                return None

                        fee = _to_float(data.get("exchange_fee"))
                        usdt_min = _to_float(data.get("usdt_min_trade"))
                        usdt_max = _to_float(data.get("usdt_max_trade"))
                        irt_min = _to_float(data.get("irt_min_trade"))
                        irt_max = _to_float(data.get("irt_max_trade"))

                        if fee is not None:
                            metadata.exchange_fee_pct = fee
                        if usdt_min is not None:
                            metadata.min_trade_usdt = usdt_min
                        if usdt_max is not None:
                            metadata.max_trade_usdt = usdt_max
                        if irt_min is not None:
                            metadata.min_trade_irt = irt_min
                        if irt_max is not None:
                            metadata.max_trade_irt = irt_max

                        # Decimals default remains 8 until a reliable source is available
                        metadata.decimals = 8

                        metadata.last_updated = datetime.now(timezone.utc)
                    else:
                        logger.debug(
                            f"SDK missing 'get_coin_info'; skipping detailed metadata for {symbol}"
                        )

                except Exception as exc:
                    logger.debug(
                        "Failed to fetch coin info symbol=%s error_type=%s",
                        symbol,
                        type(exc).__name__,
                    )
                    symbols_failed += 1

                self.symbols[symbol] = metadata
                symbols_added += 1

            self._populated = True
            logger.info(
                f"✅ Registry populated: {symbols_added} symbols added, "
                f"{symbols_failed} had partial data"
            )
            logger.info(
                f"   Leverage-enabled: {len(self.get_leverage_symbols())}, "
                f"Shortable: {len(self.get_shortable_symbols())}"
            )

        except Exception as exc:
            logger.error("Failed to populate symbol registry error_type=%s", type(exc).__name__)
            raise

    def get(self, symbol: str) -> Optional[SymbolMetadata]:
        """
        Get metadata for a symbol.

        Args:
            symbol: Symbol name (e.g., "BTC")

        Returns:
            SymbolMetadata if found, None otherwise
        """
        return self.symbols.get(symbol)

    def get_leverage_symbols(self) -> List[str]:
        """
        Get all symbols with leverage enabled.

        Returns:
            List of symbol names that support leverage trading
        """
        return [s for s, m in self.symbols.items() if m.leverage_enabled and m.active]

    def get_shortable_symbols(self) -> List[str]:
        """
        Get all symbols that can be shorted.

        Returns:
            List of symbol names that support short selling
        """
        return [s for s, m in self.symbols.items() if m.short_enabled and m.active]

    def validate_short_cap(self, symbol: str, quantity: float) -> bool:
        """
        Validate quantity against short cap.

        Args:
            symbol: Symbol to check
            quantity: Desired short quantity (positive number)

        Returns:
            True if quantity is within cap, False otherwise
        """
        metadata = self.get(symbol)
        if not metadata or not metadata.short_enabled:
            return False

        if metadata.short_cap is None:
            # No cap means unlimited
            return True

        return quantity <= metadata.short_cap

    def get_short_cap(self, symbol: str) -> Optional[float]:
        """
        Get short cap for a symbol.

        Args:
            symbol: Symbol name

        Returns:
            Short cap as float, or None if not shortable or unlimited
        """
        metadata = self.get(symbol)
        if not metadata or not metadata.short_enabled:
            return None
        return metadata.short_cap

    def is_leverage_enabled(self, symbol: str) -> bool:
        """
        Check if leverage is enabled for a symbol.

        Args:
            symbol: Symbol name

        Returns:
            True if leverage is enabled, False otherwise
        """
        metadata = self.get(symbol)
        return metadata.leverage_enabled if metadata else False

    def is_short_enabled(self, symbol: str) -> bool:
        """
        Check if short selling is enabled for a symbol.

        Args:
            symbol: Symbol name

        Returns:
            True if short selling is enabled, False otherwise
        """
        metadata = self.get(symbol)
        return metadata.short_enabled if metadata else False

    def get_all_symbols(self) -> List[str]:
        """
        Get all registered symbols.

        Returns:
            List of all symbol names
        """
        return list(self.symbols.keys())

    def get_active_symbols(self) -> List[str]:
        """
        Get all active symbols.

        Returns:
            List of active symbol names
        """
        return [s for s, m in self.symbols.items() if m.active]

    def summary(self) -> Dict[str, Any]:
        """
        Get summary statistics.

        Returns:
            Dict with summary stats
        """
        return {
            "total_symbols": len(self.symbols),
            "active_symbols": len(self.get_active_symbols()),
            "leverage_enabled": len(self.get_leverage_symbols()),
            "shortable": len(self.get_shortable_symbols()),
            "populated": self._populated,
        }


# Synchronous wrapper for compatibility
class SymbolRegistrySync:
    """
    Synchronous wrapper around SymbolRegistry.

    For use in synchronous code. Uses the async version internally.
    """

    def __init__(self, sdk_client):
        """Initialize with SDK client."""
        self.registry = SymbolRegistry(sdk_client)

    def populate(self, force_refresh: bool = False) -> None:
        """Populate registry (synchronous)."""
        import asyncio

        asyncio.run(self.registry.populate(force_refresh=force_refresh))

    def get(self, symbol: str) -> Optional[SymbolMetadata]:
        """Get symbol metadata."""
        return self.registry.get(symbol)

    def get_leverage_symbols(self) -> List[str]:
        """Get leverage-enabled symbols."""
        return self.registry.get_leverage_symbols()

    def get_shortable_symbols(self) -> List[str]:
        """Get shortable symbols."""
        return self.registry.get_shortable_symbols()

    def validate_short_cap(self, symbol: str, quantity: float) -> bool:
        """Validate short cap."""
        return self.registry.validate_short_cap(symbol, quantity)

    def summary(self) -> Dict[str, any]:
        """Get summary statistics."""
        return self.registry.summary()


if __name__ == "__main__":
    """
    Self-test.

    ⚠️  SECURITY WARNING ⚠️
    This script makes LIVE API calls to your Aban-Tether account.
    Only run this if you understand the implications and have set
    ABAN_RUN_LIVE_TEST=yes to confirm.
    """
    import os
    import sys
    import asyncio

    # Security guard: require explicit confirmation
    if os.getenv("ABAN_RUN_LIVE_TEST") != "yes":
        print("=" * 70)
        print("⚠️  SECURITY WARNING ⚠️")
        print("=" * 70)
        print("This script makes LIVE API calls to your Aban-Tether account.")
        print("")
        print("To run this test, set the environment variable:")
        print("  export ABAN_RUN_LIVE_TEST=yes")
        print("")
        print("Then run again:")
        print(f"  python {sys.argv[0]}")
        print("=" * 70)
        sys.exit(1)

    api_key = os.getenv("ABAN_API_KEY")
    if not api_key:
        print("❌ ABAN_API_KEY environment variable not set")
        sys.exit(1)

    async def test_registry():
        """Test the symbol registry."""
        from trading_gym.adapters import RateLimitedAbanSDK

        print("=" * 60)
        print("Symbol Registry - Self Test")
        print("=" * 60)

        # Initialize SDK and registry
        sdk = RateLimitedAbanSDK(api_key=api_key)
        registry = SymbolRegistry(sdk)

        # Test 1: Populate
        print("\n1. Populating registry from API...")
        await registry.populate()

        # Test 2: Get summary
        print("\n2. Registry summary:")
        summary = registry.summary()
        for key, value in summary.items():
            print(f"   {key}: {value}")

        # Test 3: Check specific symbols
        print("\n3. Checking specific symbols:")
        for symbol in ["BTC", "ETH", "SOL", "XRP"]:
            meta = registry.get(symbol)
            if meta:
                print(
                    f"   {symbol}: leverage={meta.leverage_enabled}, "
                    f"short={meta.short_enabled}, "
                    f"cap={meta.short_cap}"
                )

        # Test 4: Get filtered lists
        print("\n4. Symbol lists:")
        leverage_symbols = registry.get_leverage_symbols()
        shortable_symbols = registry.get_shortable_symbols()
        print(f"   Leverage-enabled: {len(leverage_symbols)} symbols")
        print(f"   Shortable: {len(shortable_symbols)} symbols")
        print(f"   Shortable list: {shortable_symbols}")

        # Test 5: Validate short caps
        print("\n5. Testing short cap validation:")
        test_cases = [
            ("BTC", 0.3, True),
            ("BTC", 0.6, False),
            ("ETH", 5.0, True),
            ("ETH", 15.0, False),
        ]
        for symbol, qty, expected in test_cases:
            result = registry.validate_short_cap(symbol, qty)
            status = "✅" if result == expected else "❌"
            print(f"   {status} {symbol} {qty}: {result} (expected {expected})")

        print("\n" + "=" * 60)
        print("✅ All tests completed!")
        print("=" * 60)

    asyncio.run(test_registry())
