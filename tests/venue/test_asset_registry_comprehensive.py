from trading_gym.market.venue.asset_registry import (
    is_shortable,
    is_credit_tradable,
    is_tokenized_asset,
    get_max_leverage,
    get_short_borrow_cap,
    get_asset_sector,
)


class TestAssetRegistryFunctions:
    def test_is_shortable(self):
        assert is_shortable("BTC")
        assert is_shortable("ETH")
        assert is_shortable("BTCUSDT")

        # Not shortable
        assert not is_shortable("SOL")
        assert not is_shortable("DOGE")
        assert not is_shortable("QQQX")

    def test_is_credit_tradable(self):
        # Major cryptos
        assert is_credit_tradable("BTC")
        assert is_credit_tradable("ETH")
        assert is_credit_tradable("SOL")
        assert is_credit_tradable("PEPE")

        # Tokenized assets are NOT credit tradable
        assert not is_credit_tradable("QQQX")
        assert not is_credit_tradable("TSLAX")

    def test_is_tokenized_asset(self):
        assert is_tokenized_asset("QQQX")
        assert is_tokenized_asset("TSLAX")
        assert is_tokenized_asset("SPYX")

        assert not is_tokenized_asset("BTC")

    def test_get_max_leverage_long(self):
        # Credit tradable -> 20x
        assert get_max_leverage("BTC", is_short=False) == 20.0
        assert get_max_leverage("SOL", is_short=False) == 20.0

        # Tokenized/Other -> 1x
        assert get_max_leverage("QQQX", is_short=False) == 1.0
        assert get_max_leverage("UNKNOWN", is_short=False) == 1.0

    def test_get_max_leverage_short(self):
        # Shortable -> 5x
        assert get_max_leverage("BTC", is_short=True) == 5.0
        assert get_max_leverage("ETH", is_short=True) == 5.0

        # Not shortable -> 0x (or handled elsewhere, but function returns 0.0)
        assert get_max_leverage("SOL", is_short=True) == 0.0
        assert get_max_leverage("QQQX", is_short=True) == 0.0

    def test_get_short_borrow_cap(self):
        assert get_short_borrow_cap("BTC") == 0.5
        assert get_short_borrow_cap("ETH") == 10.0

        # Others
        assert get_short_borrow_cap("SOL") == 0.0

    def test_get_asset_sector(self):
        assert get_asset_sector("BTC") == "L1"
        assert get_asset_sector("DOGE") == "MEME"
        assert get_asset_sector("ARB") == "DEFI"
        assert get_asset_sector("WLD") == "AI"
        assert get_asset_sector("QQQX") == "ETF"
