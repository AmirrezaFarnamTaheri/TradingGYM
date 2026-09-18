"""Bounded, Pydantic-validated wrapper around the Abantether SDK.

Every request passes the global rate limiter, retries only safe read methods, and
serialises through typed models so callers never handle raw venue JSON.
"""

import logging
import re
import threading
import time
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union, cast

from pydantic import BaseModel, ConfigDict, Field
from trading_gym.common.security import bounded_json_dumps, validate_base_url
from trading_gym.rate_limit.global_limiter import GlobalRateLimiter

try:
    from abantether_python_sdk.client import Client as AbantetherClient
except Exception:  # pragma: no cover - used only when SDK not installed
    # Lightweight stub so tests can patch AbantetherClient without installing SDK
    class AbantetherClient:  # type: ignore
        def __init__(self, *args, **kwargs) -> None:
            raise ImportError(
                "abantether_python_sdk is not installed. This stub exists for testing; "
                "tests should patch EnhancedAbanClient.AbantetherClient."
            )


logger = logging.getLogger(__name__)


class OrderSide(str, Enum):
    """Order side enumeration."""

    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """Order type enumeration."""

    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    """Order status enumeration."""

    NEW = "new"
    PROCESSING = "processing"
    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    FAILED = "failed"


class Balance(BaseModel):
    """User balance for a specific symbol."""

    symbol: str
    balance: Decimal = Field(..., alias="balance")
    blocked: Decimal = Field(..., alias="blocked")

    model_config = ConfigDict(populate_by_name=True)


class CoinInfo(BaseModel):
    """Detailed coin information including fees and limits."""

    symbol: str
    persian_name: str
    exchange_fee: Decimal
    usdt_min_trade: Decimal
    usdt_max_trade: Decimal
    irt_min_trade: Decimal
    irt_max_trade: Decimal
    irt_fee: Decimal
    tether_fee: Decimal


class TickerData(BaseModel):
    """Ticker data for a trading pair."""

    symbol: str
    buy_price: Decimal
    sell_price: Decimal
    buy_max: Decimal
    sell_max: Decimal
    active: bool

    @property
    def mid_price(self) -> Decimal:
        """Calculate mid price."""
        return (self.buy_price + self.sell_price) / 2

    @property
    def spread(self) -> Decimal:
        """Calculate spread in absolute terms."""
        return self.buy_price - self.sell_price

    @property
    def spread_bps(self) -> Decimal:
        """Calculate spread in basis points."""
        mid = self.mid_price
        if mid > 0:
            return (self.spread / mid) * Decimal(10000)
        return Decimal(0)


class OrderResponse(BaseModel):
    """Response from order placement."""

    id: str
    code: str
    base_symbol: str
    quote_symbol: str
    side: OrderSide
    type: OrderType
    status: OrderStatus
    volume: Dict[str, Decimal]
    filled_volume: Dict[str, Decimal]
    created_at: str
    finalized_at: Optional[str] = None
    price: Optional[Decimal] = None
    coin_price: Optional[Decimal] = None
    usdt_price: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None

    model_config = ConfigDict(use_enum_values=True)


class NetworkInfo(BaseModel):
    """Network information for deposits/withdrawals."""

    id: str
    name: str
    memo: bool
    fee: Decimal
    fee_type: str = Field(..., alias="feeType")
    min_transfer: Decimal = Field(..., alias="minTransfer")
    max_transfer: Decimal = Field(..., alias="maxTransfer")

    model_config = ConfigDict(populate_by_name=True)


class EnhancedAbanClient:
    """Typed, rate-limited client for the Abantether venue API.

    Only idempotent GETs are retried; mutating calls surface their outcome so the
    ADR-022 retry layer can decide whether a resubmission is safe.
    """

    DEFAULT_BASE_URL = "https://api.abantether.com"
    _ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
    _RETRYABLE_METHODS = frozenset({"GET"})
    _ENDPOINT_RE = re.compile(r"^/?[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?$")
    _MAX_REQUEST_BYTES = 64 * 1024
    _SENSITIVE_KEYS = frozenset(
        {
            "api_key",
            "apikey",
            "authorization",
            "cookie",
            "hash",
            "password",
            "private_key",
            "secret",
            "token",
            "wallet",
            "wallet_address",
            "walletaddress",
        }
    )

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        rate_limit_per_minute: int = 60,
        retry_attempts: int = 3,
    ):
        """Initialize a bounded wrapper around the official SDK.

        The official SDK does not expose a supported base-URL override.  A
        non-default URL is therefore rejected instead of being logged as if it
        were effective.  Automatic retries are limited to read-only GET calls;
        mutation retries could replay an order after an ambiguous network
        failure.
        """
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        if isinstance(rate_limit_per_minute, bool) or not 1 <= rate_limit_per_minute <= 600:
            raise ValueError("rate_limit_per_minute must be between 1 and 600")
        if isinstance(retry_attempts, bool) or not 1 <= retry_attempts <= 5:
            raise ValueError("retry_attempts must be between 1 and 5")

        requested_base = validate_base_url(
            base_url or self.DEFAULT_BASE_URL,
            resolve_dns=False,
        )
        if requested_base != self.DEFAULT_BASE_URL:
            raise ValueError(
                "custom base_url is unsupported by the installed Abantether SDK"
            )

        self.api_key = api_key.strip()
        self.base_url = requested_base
        self.rate_limit_per_minute = rate_limit_per_minute
        self.retry_attempts = retry_attempts

        self._client = AbantetherClient(api_key=self.api_key)
        self._request_times: List[float] = []
        self._request_times_lock = threading.Lock()
        self._global_limiter = GlobalRateLimiter.get_instance()

        logger.info(
            "Initialized EnhancedAbanClient with base_url=%s, rate_limit=%d/min",
            self.base_url,
            self.rate_limit_per_minute,
        )

    @staticmethod
    def _normalise_key(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")

    def _sanitize_payload(self, data: Any, *, depth: int = 0) -> Any:
        """Return a bounded diagnostic copy with credential-like data masked."""
        if depth > 8:
            return "***REDACTED***"
        if isinstance(data, dict):
            sanitized: Dict[str, Any] = {}
            for key, value in data.items():
                text_key = str(key)
                if self._normalise_key(text_key) in self._SENSITIVE_KEYS:
                    sanitized[text_key] = "***REDACTED***"
                else:
                    sanitized[text_key] = self._sanitize_payload(value, depth=depth + 1)
            return sanitized
        if isinstance(data, (list, tuple)):
            return [self._sanitize_payload(item, depth=depth + 1) for item in data]
        return data

    def _validate_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]],
        body: Optional[Dict[str, Any]],
    ) -> tuple[str, str]:
        normalized_method = str(method).upper().strip()
        if normalized_method not in self._ALLOWED_METHODS:
            raise ValueError(f"unsupported HTTP method: {normalized_method!r}")
        normalized_endpoint = str(endpoint).strip()
        if not self._ENDPOINT_RE.fullmatch(normalized_endpoint):
            raise ValueError("endpoint must be a safe relative API path")
        if params is not None and not isinstance(params, dict):
            raise TypeError("params must be a dictionary")
        if body is not None and not isinstance(body, dict):
            raise TypeError("body must be a dictionary")
        if normalized_method == "GET" and body:
            raise ValueError("GET requests cannot include a body")
        # Serialisation is used only as a deterministic size/depth gate.  The
        # SDK still receives the original typed values.
        bounded_json_dumps(
            {"params": params or {}, "body": body or {}},
            max_bytes=self._MAX_REQUEST_BYTES,
        )
        return normalized_method, normalized_endpoint

    def _enforce_rate_limit(self) -> None:
        """Enforce global and per-client request bounds without data races."""
        self._global_limiter.wait()
        while True:
            with self._request_times_lock:
                now = time.monotonic()
                self._request_times = [
                    timestamp
                    for timestamp in self._request_times
                    if now - timestamp < 60.0
                ]
                if len(self._request_times) < self.rate_limit_per_minute:
                    self._request_times.append(now)
                    return
                sleep_time = max(0.01, 60.0 - (now - self._request_times[0]))
            logger.warning(
                "Per-client rate limit reached (%d requests/min); delaying request",
                self.rate_limit_per_minute,
            )
            time.sleep(sleep_time)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute a validated SDK request with read-only retry semantics."""
        method, endpoint = self._validate_request(method, endpoint, params, body)
        attempts = self.retry_attempts if method in self._RETRYABLE_METHODS else 1

        def _execute_request() -> Any:
            method_name = endpoint.replace("/", "_").strip("_")
            candidate = getattr(self._client, method_name, None)
            if callable(candidate):
                return candidate(**(params or body or {}))
            fallback = getattr(self._client, "_make_request", None)
            if not callable(fallback):
                raise RuntimeError("SDK exposes neither a matching method nor a request fallback")
            return fallback(
                method=method,
                endpoint=endpoint,
                params=params,
                body=body,
            )

        safe_params = self._sanitize_payload(params)
        safe_body = self._sanitize_payload(body)
        for attempt in range(1, attempts + 1):
            self._enforce_rate_limit()
            try:
                logger.debug(
                    "Abantether request method=%s endpoint=%s params=%s body=%s attempt=%d/%d",
                    method,
                    endpoint,
                    safe_params,
                    safe_body,
                    attempt,
                    attempts,
                )
                result = _execute_request()
                logger.debug("Abantether request succeeded endpoint=%s", endpoint)
                return result
            except Exception as exc:
                # Exception strings can include URLs, response bodies, headers,
                # or tokens.  Record only the type and bounded request metadata.
                logger.warning(
                    "Abantether request failed method=%s endpoint=%s attempt=%d/%d error_type=%s",
                    method,
                    endpoint,
                    attempt,
                    attempts,
                    type(exc).__name__,
                )
                if attempt >= attempts:
                    logger.error(
                        "Abantether request exhausted method=%s endpoint=%s attempts=%d",
                        method,
                        endpoint,
                        attempts,
                    )
                    raise
                delay = min(4.0, float(2 ** (attempt - 1)))
                logger.info("Retrying read-only request after %.2f seconds", delay)
                time.sleep(delay)

        raise RuntimeError("request loop exited unexpectedly")

    @staticmethod
    def _payload_of(response: Any) -> Any:
        """Return the venue's data payload from an SDK response.

        ``_make_request`` forwards whatever the official SDK hands back — JSON
        object, list, string or ``None`` — but the accessors here all assume an
        object envelope and call ``.get`` on it. Centralising the unwrap keeps
        each accessor honest and turns ``AttributeError: 'str' object has no
        attribute 'get'`` into a single clear contract violation, the way
        ``get_deposit_history`` already did inline.
        """

        if not isinstance(response, dict):
            raise ValueError(
                "Expected a JSON object from the venue, got "
                f"{type(response).__name__}: {response!r:.120}"
            )
        return response.get("data", response)

    # ============================================================================
    # Balance & Account Methods
    # ============================================================================

    def get_balance(self) -> List[Balance]:
        """Return the available and blocked balance per currency."""
        logger.info("Fetching user balance")
        response = self._make_request("GET", "balance")

        # Handle both list and dict responses
        if isinstance(response, dict):
            balance_data = self._payload_of(response)
        else:
            balance_data = response

        balances = [Balance(**item) for item in balance_data]
        logger.info("Retrieved %d balance entries", len(balances))
        return balances

    # ============================================================================
    # Market Data Methods
    # ============================================================================

    def get_coin_info(self, symbol: str, side: OrderSide) -> CoinInfo:
        """Return fees and trade limits for ``symbol`` on the given side."""
        logger.info("Fetching coin info: symbol=%s side=%s", symbol, side)
        response = self._make_request(
            "GET",
            "coin_price",
            params={"symbol": symbol, "side": side.value},
        )
        coin_data = self._payload_of(response)
        return CoinInfo(**coin_data)

    def get_ticker(
        self, symbol: Optional[str] = None
    ) -> Union[TickerData, Dict[str, TickerData]]:
        """Return ticker data for one pair, or for every pair when ``symbol`` is None."""
        logger.info("Fetching ticker data: symbol=%s", symbol or "all")
        params = {"coin": symbol} if symbol else None
        response = self._make_request("GET", "coin_price", params=params)

        markets_data = response.get("data", {}).get("markets", {})

        if symbol:
            # Return single ticker for requested symbol
            for market, data in markets_data.items():
                if data.get("symbol") == symbol:
                    return TickerData(**data)
            raise ValueError(f"No ticker data found for symbol: {symbol}")

        # Return all tickers
        return {market: TickerData(**data) for market, data in markets_data.items()}

    # ============================================================================
    # Order Management Methods
    # ============================================================================

    def place_market_order(
        self,
        side: OrderSide,
        base_symbol: str,
        quote_symbol: str,
        volume: Union[Decimal, float],
        stop_loss_price: Optional[Union[Decimal, float]] = None,
        track_id: Optional[str] = None,
    ) -> OrderResponse:
        """Submit a market order. ``volume`` is quote currency for buys, base for sells."""
        logger.info(
            "Placing market order: side=%s base=%s quote=%s volume=%s",
            side,
            base_symbol,
            quote_symbol,
            volume,
        )

        body = {
            "side": side.value,
            "base_symbol": base_symbol,
            "quote_symbol": quote_symbol,
            "volume": str(Decimal(str(volume))),
        }

        if stop_loss_price is not None:
            body["stop_loss_price"] = str(Decimal(str(stop_loss_price)))
        if track_id:
            body["track_id"] = track_id

        response = self._make_request("POST", "place_market_order", body=body)
        order_data = self._payload_of(response)

        logger.info("Market order placed: order_id=%s", order_data.get("id"))
        return OrderResponse(**order_data)

    def place_limit_order(
        self,
        side: OrderSide,
        base_symbol: str,
        quote_symbol: str,
        price: Union[Decimal, float],
        volume: Union[Decimal, float],
        stop_loss_price: Optional[Union[Decimal, float]] = None,
        track_id: Optional[str] = None,
    ) -> OrderResponse:
        """Submit a limit order at ``price``. ``volume`` is quote currency for buys."""
        logger.info(
            "Placing limit order: side=%s base=%s quote=%s price=%s volume=%s",
            side,
            base_symbol,
            quote_symbol,
            price,
            volume,
        )

        body = {
            "side": side.value,
            "base_symbol": base_symbol,
            "quote_symbol": quote_symbol,
            "price": str(Decimal(str(price))),
            "volume": str(Decimal(str(volume))),
        }

        if stop_loss_price is not None:
            body["stop_loss_price"] = str(Decimal(str(stop_loss_price)))
        if track_id:
            body["track_id"] = track_id

        response = self._make_request("POST", "place_limit_order", body=body)
        order_data = self._payload_of(response)

        logger.info("Limit order placed: order_id=%s", order_data.get("id"))
        return OrderResponse(**order_data)

    def sell_all(
        self,
        selected_coins: List[str],
        currency: str = "IRT",
        account_type: str = "spot",
    ) -> List[OrderResponse]:
        """Flatten the holdings of ``selected_coins`` into ``currency``."""
        logger.info(
            "Selling all: coins=%s currency=%s account_type=%s",
            selected_coins,
            currency,
            account_type,
        )

        body = {
            "selected_coins": selected_coins,
            "currency": currency,
            "account_type": account_type,
        }

        response = self._make_request("POST", "sell_all", body=body)
        orders_data = self._payload_of(response)

        orders = [OrderResponse(**order) for order in orders_data]
        logger.info("Created %d sell orders", len(orders))
        return orders

    def get_orders(
        self,
        base_symbol: Optional[str] = None,
        quote_symbol: Optional[str] = None,
        side: Optional[OrderSide] = None,
        status: Optional[List[OrderStatus]] = None,
        order_type: Optional[OrderType] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        page: int = 1,
        per_page: int = 10,
    ) -> Dict[str, Any]:
        """Return a page of order history matching the supplied filters."""
        logger.info(
            "Fetching orders with filters: base=%s quote=%s", base_symbol, quote_symbol
        )

        params: Dict[str, Any] = {
            "page": page,
            "per_page": per_page,
        }

        if base_symbol:
            params["base_symbol"] = base_symbol
        if quote_symbol:
            params["quote_symbol"] = quote_symbol
        if side:
            params["side"] = side.value
        if status:
            params["status"] = [s.value for s in status]
        if order_type:
            params["order_type"] = order_type.value
        if from_date:
            params["from_date"] = from_date
        if to_date:
            params["to_date"] = to_date

        response = cast(
            Dict[str, Any], self._make_request("GET", "get_orders", params=params)
        )
        logger.info("Retrieved orders: total=%d", response.get("total", 0))
        return response

    def cancel_order(self, order_id: str) -> Dict[str, str]:
        """Cancel one order by ID."""
        logger.info("Cancelling order: order_id=%s", order_id)

        response = self._make_request(
            "POST",
            "cancel_order",
            body={"id": order_id},
        )

        logger.info("Order cancelled: order_id=%s", order_id)
        return cast(Dict[str, str], self._payload_of(response))

    def get_order_details(self, order_id: str) -> OrderResponse:
        """Return the current state of one order."""
        logger.info("Fetching order details: order_id=%s", order_id)

        response = self._make_request("GET", f"/order_handler/orders/otc/{order_id}/")
        order_data = self._payload_of(response)

        return OrderResponse(**order_data)

    # ============================================================================
    # Deposit & Withdrawal Methods
    # ============================================================================

    def get_deposit_coins(self) -> List[Dict[str, str]]:
        """List coins the venue accepts for deposit."""
        logger.info("Fetching deposit coins list")
        response = self._make_request("GET", "users/coins", params={"type": "deposit"})
        coins = response if isinstance(response, list) else response.get("data", [])
        logger.info("Retrieved %d deposit coins", len(coins))
        return coins

    def get_withdrawal_coins(self) -> List[Dict[str, str]]:
        """List coins the venue supports for withdrawal."""
        logger.info("Fetching withdrawal coins list")
        response = self._make_request("GET", "users/coins", params={"type": "withdraw"})
        coins = response if isinstance(response, list) else response.get("data", [])
        logger.info("Retrieved %d withdrawal coins", len(coins))
        return coins

    def get_networks(
        self, coin: str, transaction_type: str = "withdraw"
    ) -> List[NetworkInfo]:
        """Return the deposit/withdrawal networks available for ``coin``."""
        logger.info("Fetching networks for coin=%s type=%s", coin, transaction_type)
        response = self._make_request(
            "GET", "users/networks", params={"type": transaction_type, "coin": coin}
        )

        networks_data = (
            response if isinstance(response, list) else response.get("data", [])
        )
        networks = [NetworkInfo(**net) for net in networks_data]
        logger.info("Retrieved %d networks for %s", len(networks), coin)
        return networks

    def get_deposit_wallet(self, coin: str, network: str) -> Dict[str, str]:
        """Return the deposit address for ``coin`` on ``network``."""
        logger.info("Fetching deposit wallet for coin=%s network=%s", coin, network)
        response = self._make_request(
            "GET",
            "users/wallets",
            params={"coin": coin, "network": network, "type": "deposit"},
        )

        wallets = response if isinstance(response, list) else response.get("data", [])
        if not wallets:
            raise ValueError(f"No wallet found for {coin} on {network}")

        wallet = wallets[0]
        wallet_addr = wallet.get("wallet", "")
        # Redact wallet address for security - only show first and last 4 chars
        redacted = (
            f"{wallet_addr[:4]}...{wallet_addr[-4:]}" if len(wallet_addr) > 8 else "***"
        )
        logger.info("Retrieved deposit wallet: %s", redacted)
        return cast(Dict[str, str], wallet)

    def submit_deposit(self, coin: str, tx_hash: str) -> str:
        """Report an on-chain deposit hash to the venue for crediting."""
        # Mirror submit_withdrawal's input discipline: both values originate
        # off-chain and are user-supplied, so they get the same bounded
        # character-set checks rather than being forwarded verbatim.
        coin = str(coin).strip().upper()
        tx_hash = str(tx_hash).strip()
        if not re.fullmatch(r"[A-Z0-9]{2,16}", coin):
            raise ValueError("coin must be a safe 2-16 character symbol")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{4,128}", tx_hash):
            raise ValueError("tx_hash must be 4-128 safe transaction-hash characters")

        # Redact transaction hash for security - only show first and last 6 chars
        redacted_hash = (
            f"{tx_hash[:6]}...{tx_hash[-6:]}" if len(tx_hash) > 12 else "***"
        )
        logger.info("Submitting deposit: coin=%s hash=%s", coin, redacted_hash)
        response = self._make_request(
            "POST", "users/deposit/crypto", body={"coin": coin, "hash": tx_hash}
        )
        logger.info("Deposit submitted successfully")
        return response if isinstance(response, str) else response.get("message", "ok")

    def get_deposit_history(
        self, coin: str, page: int = 1, count: int = 20
    ) -> Dict[str, Any]:
        """Return a page of deposit history, optionally filtered by coin."""
        logger.info("Fetching deposit history: coin=%s page=%d", coin, page)
        response = self._make_request(
            "GET",
            "users/deposit/crypto/list",
            params={"coin": coin, "page": page, "count": count},
        )
        if not isinstance(response, dict):
            raise ValueError(
                f"Unexpected deposit history response type: {type(response).__name__}"
            )
        logger.info("Retrieved deposit history: total=%d", response.get("total", 0))
        return response

    def get_white_addresses(self) -> List[Dict[str, Any]]:
        """List withdrawal addresses whitelisted by the account."""
        logger.info("Fetching white addresses")
        response = self._make_request("GET", "users/white-address")
        addresses = response if isinstance(response, list) else response.get("data", [])
        logger.info("Retrieved %d white addresses", len(addresses))
        return addresses

    def get_withdrawal_fee(
        self, coin: str, network: str, amount: Union[Decimal, float]
    ) -> Decimal:
        """Return the withdrawal fee for ``amount`` of ``coin`` on ``network``.

        Raises ``RuntimeError`` on a venue error and ``ValueError`` when the fee
        payload cannot be parsed.
        """
        logger.info(
            "Calculating withdrawal fee: coin=%s network=%s amount=%s",
            coin,
            network,
            amount,
        )
        amt = Decimal(str(amount))
        response = self._make_request(
            "GET",
            "users/fee",
            params={"coin": coin, "network": network, "amount": str(amt)},
        )

        # Robustly parse response
        fee_val: Optional[Decimal] = None
        if isinstance(response, (int, float, Decimal, str)):
            try:
                fee_val = Decimal(str(response))
            except Exception:
                fee_val = None
        elif isinstance(response, dict):
            # Try common fields
            raw = response.get("fee", response.get("data"))
            if isinstance(raw, (int, float, Decimal, str)):
                try:
                    fee_val = Decimal(str(raw))
                except Exception:
                    fee_val = None

            # Surface explicit API errors if present
            if fee_val is None and (
                response.get("error") or response.get("success") is False
            ):
                msg = (
                    response.get("message")
                    or response.get("error")
                    or "unable to parse fee"
                )
                raise RuntimeError(f"Withdrawal fee error: {msg}")

        if fee_val is None:
            raise ValueError(f"Unable to parse withdrawal fee response: {response!r}")

        logger.info("Withdrawal fee: %s", fee_val)
        return fee_val

    def submit_withdrawal(
        self,
        coin: str,
        network: str,
        amount: Union[Decimal, float],
        wallet_address: str,
        memo: Optional[str] = None,
    ) -> str:
        """Submit one pre-validated withdrawal without automatic replay.

        Withdrawal is an irreversible mutation. Network admission must therefore
        succeed locally before the request is sent, and an ambiguous transport
        failure is surfaced rather than retried by ``_make_request``.
        """

        coin = str(coin).strip().upper()
        network = str(network).strip()
        wallet_address = str(wallet_address).strip()
        if not re.fullmatch(r"[A-Z0-9]{2,16}", coin):
            raise ValueError("coin must be a safe 2-16 character symbol")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", network):
            raise ValueError("network must be a safe 1-64 character identifier")
        if not re.fullmatch(r"[!-~]{4,256}", wallet_address):
            raise ValueError("wallet address must be 4-256 printable non-space characters")
        if memo is not None:
            memo = str(memo).strip()
            if not memo or len(memo) > 256 or any(ord(char) < 32 for char in memo):
                raise ValueError("memo must be 1-256 printable characters when supplied")

        amt_dec = Decimal(str(amount))
        if not amt_dec.is_finite() or amt_dec <= 0:
            raise ValueError("withdrawal amount must be finite and positive")

        redacted_addr = (
            f"{wallet_address[:6]}...{wallet_address[-6:]}"
            if len(wallet_address) > 12
            else "***"
        )
        logger.info(
            "Submitting withdrawal coin=%s network=%s amount=%s destination=%s",
            coin,
            network,
            amt_dec,
            redacted_addr,
        )

        try:
            networks = self.get_networks(coin, "withdraw")
        except Exception as exc:
            logger.error(
                "Withdrawal network admission failed error_type=%s",
                type(exc).__name__,
            )
            raise RuntimeError("withdrawal network admission is unavailable") from exc

        admitted = next((item for item in networks if item.name == network), None)
        if admitted is None:
            raise ValueError("requested withdrawal network is not admitted")
        if not all(
            value.is_finite()
            for value in (admitted.min_transfer, admitted.max_transfer, admitted.fee)
        ):
            raise RuntimeError("withdrawal network limits are invalid")
        if admitted.min_transfer < 0 or admitted.max_transfer < admitted.min_transfer:
            raise RuntimeError("withdrawal network limits are invalid")
        if amt_dec < admitted.min_transfer or amt_dec > admitted.max_transfer:
            raise ValueError("withdrawal amount is outside the admitted network limits")
        if admitted.memo and not memo:
            raise ValueError("the admitted withdrawal network requires a memo or tag")

        body = {
            "coin": coin,
            "network": network,
            "amount": str(amt_dec),
            "walletAddress": wallet_address,
        }
        if memo:
            body["memo"] = memo

        response = self._make_request("POST", "users/withdraw/crypto", body=body)
        if not isinstance(response, dict):
            raise RuntimeError("withdrawal response was not a valid object")
        if response.get("error") or response.get("success") is False:
            raise RuntimeError("withdrawal request was rejected by the venue")

        request_id: object | None = None
        for key in ("id", "requestId", "withdrawalId"):
            if response.get(key) not in (None, ""):
                request_id = response[key]
                break
        data = response.get("data")
        if request_id is None and isinstance(data, dict):
            for key in ("id", "requestId", "withdrawalId"):
                if data.get(key) not in (None, ""):
                    request_id = data[key]
                    break
        if request_id is not None:
            request_id_text = str(request_id).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", request_id_text):
                raise RuntimeError("withdrawal response identifier was invalid")
            logger.info("Withdrawal submitted request_id=%s", request_id_text)
            return request_id_text

        logger.info("Withdrawal submitted without a venue request identifier")
        return "submitted"

    def get_withdrawal_history(
        self, coin: str, page: int = 1, count: int = 20
    ) -> Dict[str, Any]:
        """Get paginated withdrawal history for ``coin``."""
        logger.info("Fetching withdrawal history: coin=%s page=%d", coin, page)
        response = self._make_request(
            "GET",
            "users/withdraw/crypto/list",
            params={"coin": coin, "page": page, "count": count},
        )
        if not isinstance(response, dict):
            raise ValueError(
                f"Unexpected withdrawal history response type: {type(response).__name__}"
            )
        logger.info("Retrieved withdrawal history: total=%d", response.get("total", 0))
        return response

    # ============================================================================
    # Margin Trading (Credit) Methods
    # ============================================================================

    def get_available_credit(
        self,
        currency: str = "IRT",
        collateral_amount: Optional[Union[Decimal, float]] = None,
    ) -> Dict[str, Any]:
        """Get available margin credit, optionally for a collateral amount."""
        logger.info("Fetching available credit: currency=%s", currency)
        params = {"currency": currency}
        if collateral_amount:
            params["collateral"] = str(Decimal(str(collateral_amount)))

        response = self._make_request("GET", "credit/available", params=params)
        return cast(Dict[str, Any], self._payload_of(response))

    def request_credit(
        self, currency: str, amount: Union[Decimal, float], leverage: int = 2
    ) -> Dict[str, Any]:
        """Request margin credit; ``leverage`` is 1-5 (default 2)."""
        # Validate inputs
        amt_dec = Decimal(str(amount))
        if amt_dec <= 0:
            raise ValueError(f"Amount must be positive, got {amt_dec}")
        if not isinstance(leverage, int) or not 1 <= leverage <= 5:
            raise ValueError(
                f"Leverage must be an integer between 1 and 5, got {leverage}"
            )

        logger.info(
            "Requesting credit: currency=%s amount=%s leverage=%dx",
            currency,
            amt_dec,
            leverage,
        )

        body = {"currency": currency, "amount": str(amount), "leverage": leverage}

        response = self._make_request("POST", "credit/request", body=body)
        if isinstance(response, dict) and (
            response.get("error") or response.get("success") is False
        ):
            err = response.get("message") or response.get("error") or "unknown error"
            raise RuntimeError(f"Credit request failed: {err}")
        logger.info("Credit requested successfully")
        return cast(Dict[str, Any], self._payload_of(response))

    def get_credit_status(self) -> Dict[str, Any]:
        """Get current credit usage, collateral, and liquidation info."""
        logger.info("Fetching credit status")
        response = self._make_request("GET", "credit/status")
        return cast(Dict[str, Any], self._payload_of(response))

    def repay_credit(
        self, amount: Optional[Union[Decimal, float]] = None
    ) -> Dict[str, Any]:
        """Repay margin credit (all outstanding if ``amount`` is None)."""
        logger.info("Repaying credit: amount=%s", amount or "all")

        body = {}
        if amount:
            body["amount"] = str(Decimal(str(amount)))

        response = self._make_request("POST", "credit/repay", body=body)
        if isinstance(response, dict) and (
            response.get("error") or response.get("success") is False
        ):
            err = response.get("message") or response.get("error") or "unknown error"
            raise RuntimeError(f"Credit repayment failed: {err}")
        logger.info("Credit repayment processed")
        return cast(Dict[str, Any], self._payload_of(response))

    # ============================================================================
    # Short Selling Methods
    # ============================================================================

    def get_shortable_coins(self) -> List[Dict[str, Any]]:
        """Get shortable coins with per-coin limits (BTC up to 0.5, ETH up to 10)."""
        logger.info("Fetching shortable coins")
        response = self._make_request("GET", "short/available-coins")
        coins = (
            self._payload_of(response) if isinstance(response, dict) else response
        )
        logger.info("Retrieved shortable coins")
        return cast(List[Dict[str, Any]], coins)

    def create_short_sell(
        self,
        base_symbol: str,
        quote_symbol: str,
        amount: Union[Decimal, float],
        leverage: int = 2,
    ) -> OrderResponse:
        """Borrow and sell ``base_symbol``; ``leverage`` is 1.5-5x (default 2)."""
        # Validate inputs
        amt_dec = Decimal(str(amount))
        if amt_dec <= 0:
            raise ValueError(f"Amount must be positive, got {amt_dec}")
        if not isinstance(leverage, int) or leverage < 1:
            raise ValueError(f"Leverage must be a positive integer, got {leverage}")

        logger.info(
            "Creating short sell: %s/%s amount=%s leverage=%dx",
            base_symbol,
            quote_symbol,
            amt_dec,
            leverage,
        )

        if base_symbol not in ["BTC", "ETH"]:
            logger.warning(
                "Short selling may not be supported for %s. "
                "Currently supports BTC and ETH.",
                base_symbol,
            )

        body = {
            "base_symbol": base_symbol,
            "quote_symbol": quote_symbol,
            "amount": str(amount),
            "leverage": leverage,
            "type": "short",
        }

        response = self._make_request("POST", "short/create", body=body)
        order_data = self._payload_of(response)

        logger.info("Short sell order created: order_id=%s", order_data.get("id"))
        return OrderResponse(**order_data)

    def get_short_positions(self) -> List[Dict[str, Any]]:
        """Get active short positions."""
        logger.info("Fetching short positions")
        response = self._make_request("GET", "short/positions")
        positions = (
            self._payload_of(response) if isinstance(response, dict) else response
        )
        logger.info(
            "Retrieved %d short positions",
            len(positions) if isinstance(positions, list) else 0,
        )
        return cast(List[Dict[str, Any]], positions)

    def close_short_position(
        self, position_id: str, amount: Optional[Union[Decimal, float]] = None
    ) -> Dict[str, Any]:
        """Buy back and return borrowed assets, closing short ``position_id``."""
        logger.info(
            "Closing short position: id=%s amount=%s", position_id, amount or "all"
        )

        body = {"position_id": position_id}
        if amount:
            body["amount"] = str(Decimal(str(amount)))

        response = self._make_request("POST", "short/close", body=body)
        if isinstance(response, dict):
            if response.get("error") or response.get("success") is False:
                err_msg = (
                    response.get("message") or response.get("error") or "unknown error"
                )
                raise RuntimeError(f"Closing short position failed: {err_msg}")
            result = self._payload_of(response)
        else:
            result = response
        logger.info("Short position closed successfully")
        return cast(Dict[str, Any], result)

    # ============================================================================
    # Historical Data Methods
    # ============================================================================

    def get_klines(
        self,
        symbol: str,
        interval: str,
        start: Optional[int] = None,
        end: Optional[int] = None,
        limit: int = 1000,
    ) -> List[List[Union[int, float, str]]]:
        """Get historical candlestick data (Klines).

        Returns [[timestamp, open, high, low, close, volume], ...] per bar.
        """
        logger.info("Fetching klines: %s %s", symbol, interval)
        params: Dict[str, Any] = {"symbol": symbol, "resolution": interval}
        if start:
            params["from"] = start
        if end:
            params["to"] = end
        if limit:
            params["count"] = limit

        # Endpoint inferred for standard exchange APIs.
        # Abantether specific endpoint might differ (e.g. /chart/history)
        response = self._make_request("GET", "market/history", params=params)

        # Handle response structure
        # Assuming: {"s": "ok", "t": [...], "o": [...], ...} (TV format)
        # or [[t,o,h,l,c,v], ...] (Binance format)

        data = self._payload_of(response)

        # If TradingView format
        if isinstance(data, dict) and "t" in data:
            return [
                [t, o, h, low, c, v]
                for t, o, h, low, c, v in zip(
                    data["t"], data["o"], data["h"], data["l"], data["c"], data["v"]
                )
            ]

        # If list of lists/dicts
        if isinstance(data, list):
            return data

        return []
