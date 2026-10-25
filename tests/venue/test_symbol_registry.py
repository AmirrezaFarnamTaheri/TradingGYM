import pytest
import unittest  # Fix: Import unittest
from unittest.mock import Mock, AsyncMock, patch
from trading_gym.market.venue.symbol_registry import SymbolRegistry, SymbolMetadata


class TestSymbolRegistry:
    @pytest.fixture
    def mock_sdk(self):
        sdk = Mock()
        # Mock get_ticker
        sdk.get_ticker = AsyncMock(
            return_value={
                "data": {
                    "markets": {
                        "BTCUSDT": {"symbol": "BTC"},
                        "ETHUSDT": {"symbol": "ETH"},
                        "XRPUSDT": {"symbol": "XRP"},
                    }
                }
            }
        )
        # Mock get_coin_info
        sdk.get_coin_info = AsyncMock(
            return_value={
                "data": {
                    "exchange_fee": "0.002",
                    "usdt_min_trade": "10",
                    "usdt_max_trade": "10000",
                }
            }
        )
        return sdk

    @pytest.fixture
    def registry(self, mock_sdk):
        # Create with a dummy config path to avoid loading real file
        with patch("builtins.open", side_effect=FileNotFoundError):
            with patch("os.path.exists", return_value=False):
                registry = SymbolRegistry(mock_sdk, config_path="dummy.json")
                # Manually inject rules for testing
                registry.leverage_symbols = {"BTC"}
                registry.short_caps = {
                    "BTC": 0.5,
                    "ETH": None,
                }  # None means unlimited but enabled
                return registry

    @pytest.mark.asyncio
    async def test_populate(self, registry):
        await registry.populate()

        assert len(registry.symbols) == 3
        assert "BTC" in registry.symbols
        assert "ETH" in registry.symbols
        assert "XRP" in registry.symbols

        btc = registry.get("BTC")
        assert btc.leverage_enabled is True
        assert btc.short_enabled is True
        assert btc.short_cap == 0.5
        assert btc.exchange_fee_pct == 0.002

        eth = registry.get("ETH")
        assert eth.leverage_enabled is False  # Not in set
        assert eth.short_enabled is True
        assert eth.short_cap is None

        xrp = registry.get("XRP")
        assert xrp.leverage_enabled is False
        assert xrp.short_enabled is False

    @pytest.mark.asyncio
    async def test_populate_skip_cached(self, registry):
        registry._populated = True
        await registry.populate()
        # Should not call API
        registry.sdk.get_ticker.assert_not_called()

        # Force refresh
        await registry.populate(force_refresh=True)
        registry.sdk.get_ticker.assert_called_once()

    @pytest.mark.asyncio
    async def test_populate_api_error(self, registry):
        registry.sdk.get_ticker.side_effect = Exception("API Fail")
        with pytest.raises(Exception):
            await registry.populate()

    @pytest.mark.asyncio
    async def test_populate_partial_failure(self, registry):
        # Ticker succeeds, but coin info fails for one
        registry.sdk.get_coin_info.side_effect = [
            {"data": {}},  # BTC success
            Exception("Info fail"),  # ETH fail
            {"data": {}},  # XRP success
        ]

        await registry.populate()

        # Should still populate all symbols
        assert "ETH" in registry.symbols
        assert registry.get("ETH").min_trade_usdt is None  # Failed to get details

    def test_validate_short_cap(self, registry):
        # Manually populate for sync testing
        registry.symbols["BTC"] = SymbolMetadata(
            symbol="BTC",
            leverage_enabled=True,
            short_enabled=True,
            short_cap=0.5,
            active=True,
        )
        registry.symbols["ETH"] = SymbolMetadata(
            symbol="ETH",
            leverage_enabled=True,
            short_enabled=True,
            short_cap=None,
            active=True,
        )
        registry.symbols["XRP"] = SymbolMetadata(
            symbol="XRP", leverage_enabled=False, short_enabled=False, active=True
        )

        # BTC: Cap 0.5
        assert registry.validate_short_cap("BTC", 0.4) is True
        assert registry.validate_short_cap("BTC", 0.5) is True
        assert registry.validate_short_cap("BTC", 0.51) is False

        # ETH: Unlimited
        assert registry.validate_short_cap("ETH", 1000.0) is True

        # XRP: Not enabled
        assert registry.validate_short_cap("XRP", 0.1) is False

        # Unknown
        assert registry.validate_short_cap("UNKNOWN", 0.1) is False

    def test_getters(self, registry):
        # Setup data
        registry.symbols["BTC"] = SymbolMetadata(
            symbol="BTC", leverage_enabled=True, short_enabled=True, active=True
        )
        registry.symbols["ETH"] = SymbolMetadata(
            symbol="ETH", leverage_enabled=False, short_enabled=True, active=True
        )
        registry.symbols["XRP"] = SymbolMetadata(
            symbol="XRP", leverage_enabled=False, short_enabled=False, active=False
        )

        assert "BTC" in registry.get_leverage_symbols()
        assert "ETH" not in registry.get_leverage_symbols()

        assert "BTC" in registry.get_shortable_symbols()
        assert "ETH" in registry.get_shortable_symbols()

        assert "XRP" not in registry.get_active_symbols()

    def test_summary(self, registry):
        registry.symbols["BTC"] = SymbolMetadata(
            symbol="BTC", leverage_enabled=True, short_enabled=True, active=True
        )
        summary = registry.summary()
        assert summary["total_symbols"] == 1
        assert summary["populated"] is False

    def test_load_rules(self):
        # Test loading from file
        with patch(
            "builtins.open",
            unittest.mock.mock_open(
                read_data='{"leverage_symbols": ["SOL"], "short_caps": {"SOL": 10.0}}'
            ),
        ):
            with patch("pathlib.Path.exists", return_value=True):
                reg = SymbolRegistry(Mock(), config_path="test.json")
                assert "SOL" in reg.leverage_symbols
                assert reg.short_caps["SOL"] == 10.0
