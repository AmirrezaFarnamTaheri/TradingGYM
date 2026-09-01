"""
Enhanced Order Pre-validation
==============================

Comprehensive pre-trade validation to catch errors before sending to exchange.

Checks:
- Min notional and lot size (price/qty decimals)
- Balance checks (quote for buys, base for sells)
- Borrow caps for shorts
- Price bands (fat-finger prevention)
- Risk limits (per-symbol exposure, concentration, leverage)
- Stop/take order trigger validation
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Optional, Set
from trading_gym.margin.borrow_caps import BorrowCapValidator, BorrowCapConfig


@dataclass
class VenueProfile:
    """Venue-specific trading rules and constraints."""

    symbol: str
    max_leverage: float
    short_enabled: bool
    short_whitelist: Set[str]
    short_caps: Dict[str, float]
    price_decimals: int
    qty_decimals: int
    min_notional: float
    min_qty: float
    max_spread_bps: float
    price_tick_size: float = 0.01  # Minimum price increment
    qty_step_size: float = 0.0001  # Minimum quantity increment


@dataclass
class AccountState:
    """Current account state for validation."""

    equity: float
    positions: Dict[str, float]
    balances: Dict[str, float]  # Available balances by symbol
    leverage: float
    price: float
    credit_wallet_active: bool
    borrowed_quote: float
    borrowed_base: float
    open_orders: Dict[str, float] = field(
        default_factory=dict
    )  # Symbol -> locked amount


@dataclass
class Order:
    """Order to be validated."""

    symbol: str
    side: str  # "buy" or "sell"
    qty: float
    price: float
    order_type: str = "market"  # "market", "limit", "stop", "take"
    stop_price: Optional[float] = None
    time_in_force: str = "GTC"  # GTC, IOC, FOK, etc.


@dataclass
class RiskLimits:
    """Risk limits for position sizing."""

    max_position_value: float = float("inf")  # Max value per symbol
    max_concentration_pct: float = 50.0  # Max % of portfolio in one symbol
    max_total_leverage: float = 5.0
    max_order_value: float = float("inf")  # Max value per order
    fat_finger_threshold_pct: float = 10.0  # Max % deviation from market price


class OrderRejection(Exception):
    """Raised when order fails pre-validation."""

    def __init__(self, reason: str, code: str = "VALIDATION_ERROR"):
        self.reason = reason
        self.code = code
        super().__init__(reason)


class EnhancedOrderValidator:
    """
    Enhanced pre-trade validator with comprehensive checks.

    Example:
        >>> profile = VenueProfile(...)
        >>> account = AccountState(...)
        >>> validator = EnhancedOrderValidator(profile, account)
        >>> order = Order("BTC", "buy", 0.1, 50000.0)
        >>> validator.validate(order)  # Raises OrderRejection if invalid
    """

    def __init__(
        self,
        venue_profile: VenueProfile,
        account_state: AccountState,
        borrow_cap_config: BorrowCapConfig,
        risk_limits: Optional[RiskLimits] = None,
    ):
        self.venue = venue_profile
        self.account = account_state
        self.borrow_cap_validator = BorrowCapValidator(borrow_cap_config)
        self.risk_limits = risk_limits or RiskLimits()

    def validate(self, order: Order) -> None:
        """
        Validate order against all pre-trade rules.

        Raises:
            OrderRejection: If order fails any validation check
        """
        # Run all validation checks
        self._validate_decimals(order)
        self._validate_min_sizes(order)
        self._validate_price_bands(order)
        self._validate_balances(order)
        self._validate_leverage(order)
        self._validate_short_rules(order)
        self._validate_risk_limits(order)
        self._validate_stop_take_orders(order)
        self._validate_dust(order)

    def _extract_quote_symbol(
        self, symbol: str, default_quote: Optional[str] = None
    ) -> str:
        """Extract quote currency from trading symbol with normalization."""
        if default_quote is None:
            default_quote = "USDT"
        default_quote = default_quote.strip().upper()

        if symbol:
            normalized_symbol = symbol.strip().upper()
            if "/" in normalized_symbol:
                parts = [part.strip() for part in normalized_symbol.split("/")]
                if len(parts) == 2 and parts[1]:
                    return parts[1]

        # Fallback to configured default quote for legacy symbol format
        return default_quote

    def _validate_decimals(self, order: Order) -> None:
        """Validate price and quantity decimals match venue requirements."""
        # Check price decimals
        price_str = f"{order.price:.10f}".rstrip("0").rstrip(".")
        if "." in price_str:
            price_decimals = len(price_str.split(".")[1])
            if price_decimals > self.venue.price_decimals:
                raise OrderRejection(
                    f"Price has too many decimals: {price_decimals} > {self.venue.price_decimals}",
                    code="INVALID_PRICE_PRECISION",
                )

        # Check quantity decimals
        qty_str = f"{order.qty:.10f}".rstrip("0").rstrip(".")
        if "." in qty_str:
            qty_decimals = len(qty_str.split(".")[1])
            if qty_decimals > self.venue.qty_decimals:
                raise OrderRejection(
                    f"Quantity has too many decimals: {qty_decimals} > {self.venue.qty_decimals}",
                    code="INVALID_QTY_PRECISION",
                )

        # Check tick size
        price_decimal = Decimal(str(order.price))
        tick_size = Decimal(str(self.venue.price_tick_size))
        if price_decimal % tick_size != 0:
            raise OrderRejection(
                f"Price {order.price} not aligned with tick size {self.venue.price_tick_size}",
                code="INVALID_TICK_SIZE",
            )

        # Check step size
        qty_decimal = Decimal(str(order.qty))
        step_size = Decimal(str(self.venue.qty_step_size))
        if qty_decimal % step_size != 0:
            raise OrderRejection(
                f"Quantity {order.qty} not aligned with step size {self.venue.qty_step_size}",
                code="INVALID_STEP_SIZE",
            )

    def _validate_min_sizes(self, order: Order) -> None:
        """Validate minimum notional and quantity."""
        notional = order.qty * order.price

        if notional < self.venue.min_notional:
            raise OrderRejection(
                f"Order notional {notional:.2f} below minimum {self.venue.min_notional}",
                code="BELOW_MIN_NOTIONAL",
            )

        if abs(order.qty) < self.venue.min_qty:
            raise OrderRejection(
                f"Order quantity {abs(order.qty)} below minimum {self.venue.min_qty}",
                code="BELOW_MIN_QTY",
            )

    def _validate_price_bands(self, order: Order) -> None:
        """Validate price is within reasonable bands (fat-finger check)."""
        if order.order_type == "market":
            return  # Market orders don't have price validation

        current_price = self.account.price
        if current_price <= 0:
            return  # Can't validate without market price

        price_deviation_pct = abs(order.price - current_price) / current_price * 100

        if price_deviation_pct > self.risk_limits.fat_finger_threshold_pct:
            raise OrderRejection(
                f"Price {order.price} deviates {price_deviation_pct:.2f}% from market {current_price} "
                f"(threshold: {self.risk_limits.fat_finger_threshold_pct}%)",
                code="PRICE_OUT_OF_BAND",
            )

    def _validate_balances(self, order: Order) -> None:
        """Validate sufficient balances for the trade."""
        if order.side == "buy":
            # Need quote currency (e.g., USDT, IRT)
            # Extract quote symbol from trading pair (e.g., "BTC/USDT" -> "USDT")
            # Falls back to USDT for legacy symbol format
            quote_symbol = self._extract_quote_symbol(order.symbol)

            # Account for existing locked amounts in open orders
            available = self.account.balances.get(quote_symbol, 0.0)
            locked = self.account.open_orders.get(quote_symbol, 0.0)
            required = order.qty * order.price

            if available - locked < required:
                raise OrderRejection(
                    f"Insufficient {quote_symbol} balance: available={available:.2f} "
                    f"locked={locked:.2f} required={required:.2f}",
                    code="INSUFFICIENT_BALANCE",
                )

        elif order.side == "sell":
            # Need base currency (e.g., BTC)
            available = self.account.balances.get(order.symbol, 0.0)
            locked = self.account.open_orders.get(order.symbol, 0.0)
            current_position = self.account.positions.get(order.symbol, 0.0)

            # For sell, we need to own the asset (or be able to borrow it)
            owned = current_position + available
            required = order.qty

            if owned - locked < required:
                # Check if short selling is allowed
                if not (
                    self.venue.short_enabled
                    and order.symbol in self.venue.short_whitelist
                ):
                    raise OrderRejection(
                        f"Insufficient {order.symbol} balance for sell: "
                        f"owned={owned:.6f} locked={locked:.6f} required={required:.6f}",
                        code="INSUFFICIENT_BALANCE",
                    )

    def _validate_leverage(self, order: Order) -> None:
        """Validate leverage constraints."""
        current_position = self.account.positions.get(order.symbol, 0.0)
        new_position = current_position + (
            order.qty if order.side == "buy" else -order.qty
        )
        new_exposure = abs(new_position) * order.price

        if self.account.equity <= 0:
            raise OrderRejection("Zero or negative equity", code="ZERO_EQUITY")

        new_leverage = new_exposure / self.account.equity

        if new_leverage > self.venue.max_leverage:
            raise OrderRejection(
                f"Order would exceed max leverage: {new_leverage:.2f}x > {self.venue.max_leverage}x",
                code="LEVERAGE_EXCEEDED",
            )

        if new_leverage > self.risk_limits.max_total_leverage:
            raise OrderRejection(
                f"Order would exceed risk limit leverage: {new_leverage:.2f}x > "
                f"{self.risk_limits.max_total_leverage}x",
                code="RISK_LIMIT_LEVERAGE",
            )

    def _validate_short_rules(self, order: Order) -> None:
        """Validate short selling rules."""
        if order.side != "sell":
            return

        current_position = self.account.positions.get(order.symbol, 0.0)
        new_position = current_position - order.qty

        # If new position is negative, it's a short
        if new_position < 0:
            if not self.venue.short_enabled:
                raise OrderRejection(
                    "Short selling is disabled",
                    code="SHORT_DISABLED",
                )

            if order.symbol not in self.venue.short_whitelist:
                raise OrderRejection(
                    f"Short selling not allowed for {order.symbol}",
                    code="SHORT_NOT_WHITELISTED",
                )

            # Check borrow cap
            short_qty = abs(new_position)
            is_valid, reason, msg = self.borrow_cap_validator.validate_short(
                order.symbol, short_qty
            )
            if not is_valid:
                raise OrderRejection(msg, code="BORROW_CAP_EXCEEDED")

    def _validate_risk_limits(self, order: Order) -> None:
        """Validate position-level risk limits."""
        # Check max order value
        order_value = abs(order.qty) * order.price
        if order_value > self.risk_limits.max_order_value:
            raise OrderRejection(
                f"Order value {order_value:.2f} exceeds max {self.risk_limits.max_order_value:.2f}",
                code="ORDER_TOO_LARGE",
            )

        # Check max position value
        current_position = self.account.positions.get(order.symbol, 0.0)
        new_position = current_position + (
            order.qty if order.side == "buy" else -order.qty
        )
        new_position_value = abs(new_position) * order.price

        if new_position_value > self.risk_limits.max_position_value:
            raise OrderRejection(
                f"Position value {new_position_value:.2f} exceeds max "
                f"{self.risk_limits.max_position_value:.2f}",
                code="POSITION_TOO_LARGE",
            )

        # Check concentration limit
        if self.account.equity > 0:
            concentration_pct = (new_position_value / self.account.equity) * 100
            if concentration_pct > self.risk_limits.max_concentration_pct:
                raise OrderRejection(
                    f"Position concentration {concentration_pct:.2f}% exceeds max "
                    f"{self.risk_limits.max_concentration_pct}%",
                    code="CONCENTRATION_EXCEEDED",
                )

    def _validate_stop_take_orders(self, order: Order) -> None:
        """Validate stop and take profit orders."""
        if order.order_type not in ["stop", "take", "stop_limit", "take_limit"]:
            return

        if order.stop_price is None:
            raise OrderRejection(
                "Stop/take orders require stop_price",
                code="MISSING_STOP_PRICE",
            )

        current_price = self.account.price
        if current_price <= 0:
            # Can't validate without market price
            return

        # For stop-loss: stop price should be below current (for longs)
        # For take-profit: stop price should be above current (for longs)
        if order.order_type in ["stop", "stop_limit"]:
            if order.side == "sell" and order.stop_price >= current_price:
                raise OrderRejection(
                    f"Stop-loss price {order.stop_price} should be below current {current_price}",
                    code="INVALID_STOP_PRICE",
                )
            elif order.side == "buy" and order.stop_price <= current_price:
                raise OrderRejection(
                    f"Stop-buy price {order.stop_price} should be above current {current_price}",
                    code="INVALID_STOP_PRICE",
                )

        elif order.order_type in ["take", "take_limit"]:
            if order.side == "sell" and order.stop_price <= current_price:
                raise OrderRejection(
                    f"Take-profit price {order.stop_price} should be above current {current_price}",
                    code="INVALID_TAKE_PRICE",
                )
            elif order.side == "buy" and order.stop_price >= current_price:
                raise OrderRejection(
                    f"Take-profit buy price {order.stop_price} should be below current {current_price}",
                    code="INVALID_TAKE_PRICE",
                )

    def _validate_dust(self, order: Order) -> None:
        """Validate that order won't create dust positions."""
        # After trade, check if remaining position is below min_qty
        current_position = self.account.positions.get(order.symbol, 0.0)
        new_position = current_position + (
            order.qty if order.side == "buy" else -order.qty
        )

        # If position is non-zero but below min_qty, it's dust
        if 0 < abs(new_position) < self.venue.min_qty:
            raise OrderRejection(
                f"Order would create dust position: {abs(new_position):.6f} < {self.venue.min_qty}",
                code="DUST_POSITION",
            )


# Legacy compatibility wrapper
class MarginTradeValidator(EnhancedOrderValidator):
    """Legacy validator for backward compatibility."""

    def validate_order(self, order: Order):
        """Legacy method name."""
        return self.validate(order)


def validate_order(
    order: Order,
    venue: VenueProfile,
    account: AccountState,
    borrow_cap_config: BorrowCapConfig,
    risk_limits: Optional[RiskLimits] = None,
):
    """
    Convenience function to validate an order.
    """
    validator = EnhancedOrderValidator(venue, account, borrow_cap_config, risk_limits)
    validator.validate(order)
