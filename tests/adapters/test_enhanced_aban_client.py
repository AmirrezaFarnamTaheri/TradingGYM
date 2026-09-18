import unittest
from unittest.mock import Mock, patch
from decimal import Decimal
from trading_gym.adapters.enhanced_aban_client import (
    EnhancedAbanClient,
    OrderSide,
)


class TestEnhancedAbanClient(unittest.TestCase):
    def setUp(self):
        self.api_key = "test-key"
        self.mock_sdk_client = Mock()

        # Mock the SDK client class to return our mock instance
        self.patcher = patch(
            "trading_gym.adapters.enhanced_aban_client.AbantetherClient",
            return_value=self.mock_sdk_client,
        )
        self.patcher.start()

        # We also need to mock GlobalRateLimiter to avoid sleeps
        self.limiter_patcher = patch(
            "trading_gym.adapters.enhanced_aban_client.GlobalRateLimiter"
        )
        self.mock_limiter_cls = self.limiter_patcher.start()
        self.mock_limiter = self.mock_limiter_cls.get_instance.return_value
        self.mock_limiter.execute_request.side_effect = lambda f, **k: f()

        self.client = EnhancedAbanClient(api_key=self.api_key)

    def tearDown(self):
        self.patcher.stop()
        self.limiter_patcher.stop()

    def test_init(self):
        self.assertEqual(self.client.api_key, "test-key")

    def test_sanitize_payload(self):
        payload = {
            "public": "value",
            "token": "secret123",
            "wallet": "0x123",
            "nested": {"password": "pass"},
        }
        sanitized = self.client._sanitize_payload(payload)
        self.assertEqual(sanitized["public"], "value")
        self.assertEqual(sanitized["token"], "***REDACTED***")

    def test_payload_of_unwraps_the_envelope_and_rejects_non_objects(self):
        # An envelope carrying a data payload unwraps to the payload.
        self.assertEqual(self.client._payload_of({"data": {"id": 1}}), {"id": 1})
        # A bare object is its own payload.
        self.assertEqual(self.client._payload_of({"id": 1}), {"id": 1})
        # The SDK returns whatever it returns; an accessor that blindly called
        # .get() on these raised AttributeError, not a usable contract error.
        for bad in ("not-an-object", None, 42, ["a"]):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.client._payload_of(bad)

    def test_accessors_surface_a_non_object_response_as_a_contract_error(self):
        # get_coin_info forwards the raw SDK return straight to the payload
        # boundary; a non-object must fail with the typed error rather than
        # an AttributeError deep inside the accessor.
        self.mock_sdk_client.coin_price = Mock(return_value="not-an-object")
        with self.assertRaises(ValueError):
            self.client.get_coin_info("BTC", OrderSide.BUY)

    def test_get_balance(self):
        mock_data = [
            {"symbol": "BTC", "balance": "1.5", "blocked": "0.1"},
            {"symbol": "USDT", "balance": "1000.0", "blocked": "0.0"},
        ]
        # balance endpoint -> method 'balance' or 'wallet_balance'?
        # The dynamic dispatch: endpoint "balance" -> method "balance"
        # Wait, get_balance calls _make_request("GET", "balance")?
        # Let's check get_balance implementation again?
        # Previous grep showed: response = self._make_request("GET", "balance")
        # So it looks for self._client.balance

        self.mock_sdk_client.balance = Mock(return_value={"data": mock_data})

        balances = self.client.get_balance()
        self.assertEqual(len(balances), 2)
        self.assertEqual(balances[0].symbol, "BTC")
        self.assertEqual(balances[0].balance, Decimal("1.5"))

    def test_place_limit_order(self):
        mock_response = {
            "data": {
                "id": "ord124",
                "code": "124",
                "base_symbol": "BTC",
                "quote_symbol": "USDT",
                "side": "buy",
                "type": "limit",
                "status": "new",
                "volume": {"amount": "0.1"},
                "filled_volume": {"amount": "0.0"},
                "created_at": "2024-01-01T00:00:00Z",
            }
        }
        # Endpoint 'orders/limit/buy' -> method 'place_limit_order'
        # Or 'orders/limit/sell' -> 'orders_limit_sell'
        # place_limit_order calls:
        # "orders/limit/{side.value}"
        # So "orders/limit/buy" -> place_limit_order

        self.mock_sdk_client.place_limit_order = Mock(return_value=mock_response)

        order = self.client.place_limit_order(
            side=OrderSide.BUY,
            base_symbol="BTC",
            quote_symbol="USDT",
            price=50000,
            volume=0.1,
        )
        self.assertEqual(order.id, "ord124")

    def test_create_short_sell(self):
        mock_resp = {
            "data": {
                "id": "short123",
                "code": "s1",
                "base_symbol": "BTC",
                "quote_symbol": "IRT",
                "side": "sell",
                "type": "market",  # Short create uses market or limit? The endpoint is short/create
                "status": "new",
                "volume": {"total": "0.1"},
                "filled_volume": {"total": "0.0"},
                "created_at": "2024-01-01",
            }
        }
        # Endpoint 'short/create' -> short_create
        self.mock_sdk_client.short_create = Mock(return_value=mock_resp)

        order = self.client.create_short_sell(
            base_symbol="BTC", quote_symbol="IRT", amount=0.1, leverage=2
        )
        self.assertEqual(order.id, "short123")

    def test_request_credit(self):
        mock_resp = {"data": {"id": "cred1", "status": "success"}}
        # Endpoint 'credit/request' -> credit_request
        self.mock_sdk_client.credit_request = Mock(return_value=mock_resp)

        res = self.client.request_credit("USDT", 1000, leverage=3)
        self.assertEqual(res["id"], "cred1")

    def test_get_klines(self):
        # Endpoint 'market/history' -> market_history
        mock_tv = {
            "s": "ok",
            "t": [1600000000, 1600000060],
            "o": [100, 101],
            "h": [102, 103],
            "l": [99, 100],
            "c": [101, 102],
            "v": [10, 12],
        }
        self.mock_sdk_client.market_history = Mock(return_value={"data": mock_tv})

        klines = self.client.get_klines("BTC", "1m")
        self.assertEqual(len(klines), 2)

    @patch("trading_gym.adapters.enhanced_aban_client.time.sleep", return_value=None)
    def test_get_request_retries_transient_failure(self, _sleep):
        self.mock_sdk_client.balance = Mock(
            side_effect=[TimeoutError("temporary"), {"data": []}]
        )

        result = self.client._make_request("GET", "balance")

        self.assertEqual(result, {"data": []})
        self.assertEqual(self.mock_sdk_client.balance.call_count, 2)

    @patch("trading_gym.adapters.enhanced_aban_client.time.sleep", return_value=None)
    def test_post_request_is_never_automatically_retried(self, _sleep):
        self.mock_sdk_client.place_market_order = Mock(
            side_effect=TimeoutError("ambiguous order result")
        )

        with self.assertRaises(TimeoutError):
            self.client._make_request(
                "POST", "place_market_order", body={"base_symbol": "BTC"}
            )

        self.assertEqual(self.mock_sdk_client.place_market_order.call_count, 1)

    @patch("trading_gym.adapters.enhanced_aban_client.time.sleep", return_value=None)
    def test_exception_content_is_not_logged(self, _sleep):
        self.mock_sdk_client.balance = Mock(
            side_effect=RuntimeError("authorization=super-secret-token")
        )
        with self.assertLogs(
            "trading_gym.adapters.enhanced_aban_client", level="WARNING"
        ) as captured:
            with self.assertRaises(RuntimeError):
                self.client._make_request("GET", "balance")

        output = "\n".join(captured.output)
        self.assertNotIn("super-secret-token", output)
        self.assertIn("RuntimeError", output)

    def test_request_validation_rejects_unsafe_metadata(self):
        for method, endpoint in [
            ("TRACE", "balance"),
            ("GET", "../balance"),
            ("GET", "balance?token=secret"),
            ("GET", "https://example.com/balance"),
        ]:
            with self.subTest(method=method, endpoint=endpoint):
                with self.assertRaises(ValueError):
                    self.client._make_request(method, endpoint)

    def test_request_size_is_bounded(self):
        with self.assertRaises(ValueError):
            self.client._make_request(
                "POST",
                "place_market_order",
                body={"payload": "x" * (64 * 1024)},
            )

    def test_constructor_rejects_unsupported_or_unbounded_configuration(self):
        with self.assertRaises(ValueError):
            EnhancedAbanClient(api_key="")
        with self.assertRaises(ValueError):
            EnhancedAbanClient(api_key="key", rate_limit_per_minute=0)
        with self.assertRaises(ValueError):
            EnhancedAbanClient(api_key="key", retry_attempts=6)
        with self.assertRaises(ValueError):
            EnhancedAbanClient(api_key="key", base_url="https://example.com")
