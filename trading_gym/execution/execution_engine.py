from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import numpy as np
from trading_gym.common.config import EnvConfig
from trading_gym.execution.fill_models import (
    FillResult,
    LimitOrderBook,
    book_total_liquidity,
    market_fill,
)

logger = logging.getLogger(__name__)

# Constants for high-performance Decimal math
ZERO = Decimal("0")
EPSILON = Decimal("1e-12")
BP_DIVISOR = Decimal("10000")
TWO = Decimal("2")


@dataclass(slots=True)
class MarketSnapshot:
    """Single-bar market data (floats from data feed).

    ``depth_book`` optionally carries an L2 :class:`LimitOrderBook` (asks
    best-first ascending, bids best-first descending — caller's duty).  When
    present, :meth:`ExecutionEngine.apply_trade` fills by walking that depth
    instead of the parametric spread/impact model, and the walked liquidity
    is intersected with the venue and configured notional caps.
    """

    mid_price: float
    spread_bps: float
    volatility_hint: float
    buy_cap_quote: float
    sell_cap_quote: float
    depth_book: LimitOrderBook | None = None


@dataclass(slots=True)
class ExecutionState:
    """Mutable state tracked using STRICT DECIMAL precision."""

    position: Decimal
    equity: Decimal
    borrowed_base: Decimal
    borrowed_quote: Decimal
    average_entry_price: Decimal


@dataclass(slots=True)
class ExecutionResult:
    """Outcome of applying a trade decision."""

    state: ExecutionState
    delta_units: Decimal
    executed_price: Decimal
    fee_cost: Decimal
    slippage_cost: Decimal
    crossing_cost: Decimal
    liquidity_capped: bool
    risk_reduction_only: bool = False

    @property
    def total_cost(self) -> Decimal:
        return self.fee_cost + self.slippage_cost + self.crossing_cost


class ExecutionEngine:
    """Shared Decimal execution/accounting with explicit local assumptions.

    Without a depth book the local parametric cost model uses
    ``slip_vol = volatility_lambda * abs(delta_units) * volatility_hint`` and
    ``slip_liq = liquidity_lambda * sqrt(mid_notional / liquidity_cap)``.
    Their sum is multiplied by fill notional; spread crossing and fees are
    separate costs. Coefficients depend on the instrument and input units.
    No venue calibration or source-backed universal coefficient range is
    established here. This exact formula is not attributed to the linear-cost
    Almgren--Chriss (2001) execution model.

    With a book, the final projected quantity is re-walked against supplied
    resting levels and intersected with declared notional limits. This is a
    deterministic depth assumption, not evidence of queue position or a real
    venue fill. Costs retain the existing nonnegative-shortfall convention.

    Equity is marked at ``market.mid_price``. Quote borrowing is the negative
    marked cash balance, ``max(0, position * mid - equity)``. Margin admission
    conservatively uses ``max(mid, fill_price)``; that risk valuation is not a
    second cash debit and must not be used to manufacture a larger loan.
    Individual fill prices are rounded to ticks; weighted entry price retains
    Decimal precision because an average of legal fills need not be on a tick.
    """

    def __init__(self, cfg: EnvConfig):
        self.cfg = cfg
        self._decimals = cfg.decimals
        self._base_symbol = (
            cfg.symbol.upper()
            .replace(cfg.quote_ccy.upper(), "")
            .replace(f"-{cfg.quote_ccy.upper()}", "")
        )
        self._allowed_short_symbols = {s.upper() for s in cfg.shorts.allowed_symbols}

        # Pre-convert config constants to Decimal to prevent TypeErrors in the loop
        self._taker_fee = Decimal(str(self.cfg.costs.taker_fee_bps)) / BP_DIVISOR
        self._vol_lambda = Decimal(str(self.cfg.slippage.volatility_lambda))
        self._liq_lambda = Decimal(str(self.cfg.slippage.liquidity_lambda))
        self._max_lev = Decimal(str(max(self.cfg.leverage.max_leverage, 1e-9)))
        self._min_notional = Decimal(
            str(self._decimals.min_notional_for(self.cfg.symbol))
        )
        self._min_qty = Decimal(str(self._decimals.min_qty_for(self.cfg.symbol)))
        # Invalid or negative costs manufacture trading profits rather than
        # describing a plausible execution. Reject them at construction time.
        if any(
            not amount.is_finite() or amount < ZERO
            for amount in (self._taker_fee, self._vol_lambda, self._liq_lambda)
        ):
            raise ValueError(
                "execution fee/slippage costs must be finite and non-negative"
            )
        if any(
            not minimum.is_finite() or minimum < ZERO
            for minimum in (self._min_notional, self._min_qty)
        ):
            raise ValueError("execution minimum quantity/notional must be non-negative")

        # Shorting config
        self._shorts_enabled = self.cfg.shorts.enabled

        cap_val = self.cfg.shorts.borrow_caps.get(self._base_symbol, 0.0)
        self._short_cap = Decimal(str(cap_val))
        self._max_short_lev = Decimal(
            str(max(self.cfg.leverage.max_short_leverage, 1e-9))
        )
        # Broker-faithful margin multiplier (contract_size x margin_rate).
        # Identity (1.0) unless the symbol declares explicit overrides.
        contract_size = self.cfg.leverage.contract_sizes.get(
            self._base_symbol,
            self.cfg.leverage.contract_sizes.get(self.cfg.symbol, 1.0),
        )
        margin_rate = self.cfg.leverage.margin_rates.get(
            self._base_symbol,
            self.cfg.leverage.margin_rates.get(self.cfg.symbol, 1.0),
        )
        if contract_size <= 0 or margin_rate <= 0:
            raise ValueError("contract_size and margin_rate must be positive")
        self._margin_multiplier = Decimal(str(contract_size * margin_rate))

    def apply_trade(
        self,
        state: ExecutionState,
        target_position: float,
        market: MarketSnapshot,
    ) -> ExecutionResult:
        # 0. Validate state and market inputs before any arithmetic. A malformed
        # feed must not create a synthetic trade, negative price, or NaN state.
        state_values = (
            state.position,
            state.equity,
            state.borrowed_base,
            state.borrowed_quote,
            state.average_entry_price,
        )
        if not all(value.is_finite() for value in state_values):
            raise ValueError("execution state contains non-finite values")
        if (
            state.equity < ZERO
            or state.borrowed_base < ZERO
            or state.borrowed_quote < ZERO
        ):
            raise ValueError("execution state contains negative balances")
        market_core_values = (
            market.mid_price,
            market.spread_bps,
            market.volatility_hint,
        )
        if not all(np.isfinite(float(value)) for value in market_core_values):
            raise ValueError("market snapshot contains non-finite values")
        # Liquidity caps may be +inf (unbounded venue liquidity); NaN is still invalid.
        if np.isnan(float(market.buy_cap_quote)) or np.isnan(
            float(market.sell_cap_quote)
        ):
            raise ValueError("market liquidity caps must not be NaN")
        if market.mid_price <= 0:
            raise ValueError("market mid price must be positive")
        if market.spread_bps < 0 or market.spread_bps >= 20000:
            # A non-positive bid is impossible for a positive mid; the prior
            # sell path could fabricate negative fill prices and negative fees.
            raise ValueError("market spread implies a non-positive bid price")
        # volatility_hint is advisory: apply_trade clamps negatives to zero
        # below, so only non-finite hints are rejected outright.
        if market.volatility_hint < 0:
            logger.debug(
                "Negative volatility hint treated as zero: %s", market.volatility_hint
            )
        if market.buy_cap_quote < 0 or market.sell_cap_quote < 0:
            raise ValueError("market liquidity caps must be non-negative")

        # 1. Robustness: Check for NaN/Inf (Safely handle Decimal)
        try:
            target_float = float(target_position)
            if np.isnan(target_float) or np.isinf(target_float):
                logger.warning(
                    "Invalid target position: %s. Treating as no-op.", target_position
                )
                return self._no_trade_result(
                    state, Decimal(str(market.mid_price)), False
                )
        except (TypeError, ValueError):
            # If conversion fails, assume invalid
            logger.warning(
                "Invalid target type: %s. Treating as no-op.", type(target_position)
            )
            return self._no_trade_result(state, Decimal(str(market.mid_price)), False)

        # Project once onto the intersection of quantity, liquidity, borrow and
        # post-cost margin constraints. Later clipping must never enlarge an
        # already capped fill or price a changed fill using an old book walk.
        target = Decimal(str(target_position))
        mid_price = Decimal(str(market.mid_price))
        if abs(target) < self._min_qty:
            target = ZERO
        if target < ZERO:
            target = -self._enforce_short_rules(abs(target))
        qty_decimals = self._decimals.qty_for(self.cfg.symbol)
        price_decimals = self._decimals.price_for(self.cfg.symbol)
        quantum = Decimal(f"1e-{qty_decimals}")
        requested_delta = target - state.position
        if target != ZERO:
            requested_delta = self._round_qty(requested_delta, qty_decimals)
        if requested_delta == ZERO:
            return self._no_trade_result(state, mid_price)
        direction = Decimal("1") if requested_delta > ZERO else Decimal("-1")
        liquidity_cap = self._liquidity_cap(requested_delta, market)
        if liquidity_cap <= ZERO:
            return self._no_trade_result(state, mid_price, True)
        upper = min(abs(requested_delta), liquidity_cap / mid_price)
        if market.depth_book is not None:
            initial_fill, _ = self._depth_fill(requested_delta, market.depth_book)
            if initial_fill is None:
                return self._no_trade_result(state, mid_price, True)
            upper = min(upper, initial_fill.filled_quantity)
        liquidity_capped = upper < abs(requested_delta)
        # Exact closure of legacy sub-tick dust is an accounting boundary.
        exact_close = target == ZERO and upper == abs(requested_delta)
        if not exact_close:
            upper = self._round_qty(upper, qty_decimals)
        if upper <= ZERO:
            return self._no_trade_result(state, mid_price, liquidity_capped)
        half_spread = Decimal(str(market.spread_bps)) / BP_DIVISOR * mid_price / TWO
        vol_hint = Decimal(str(max(market.volatility_hint, 0)))

        def quote(amount: Decimal):
            d = direction * amount
            if market.depth_book is not None:
                fill, _ = self._depth_fill(d, market.depth_book)
                if fill is None or fill.filled_quantity < amount:
                    return None
                px = self._round_price(fill.average_price, price_decimals)
            else:
                px = self._round_price(
                    mid_price + direction * half_spread, price_decimals
                )
            if not px.is_finite() or px <= ZERO:
                raise ValueError("computed execution price must be positive")
            notional = amount * px
            fee = notional * self._taker_fee
            if market.depth_book is not None:
                slippage, crossing = amount * abs(px - mid_price), ZERO
            else:
                slip_vol = self._vol_lambda * amount * vol_hint
                slip_liq = (
                    self._liq_lambda * (amount * mid_price / liquidity_cap).sqrt()
                )
                slippage, crossing = (
                    amount * px * (slip_vol + slip_liq),
                    amount * half_spread,
                )
            equity = state.equity - fee - slippage - crossing
            position = state.position + d
            return px, fee, slippage, crossing, equity, position

        def feasible(amount: Decimal) -> bool:
            if amount <= ZERO:
                return True
            q = quote(amount)
            if q is None:
                return False
            px, _, _, _, equity, position = q
            if amount * px > liquidity_cap or equity < ZERO:
                return False
            limit = self._max_lev if position >= ZERO else self._max_short_lev
            margin_price = max(mid_price, px)
            within_margin = (
                abs(position) * margin_price * self._margin_multiplier <= equity * limit
            )
            short_allowed = position >= ZERO or abs(
                position
            ) <= self._enforce_short_rules(abs(position))
            if within_margin and short_allowed:
                return True
            # An already over-limit account may reduce risk partially when the
            # book cannot fill a complete cure. Never manufacture extra liquidity.
            same_side = position == ZERO or position * state.position > ZERO
            reducing = same_side and abs(position) < abs(state.position)
            improves_margin = (
                abs(position) * state.equity <= abs(state.position) * equity
            )
            return reducing and improves_margin

        amount = upper
        if not feasible(amount):
            low, high = ZERO, upper
            for _ in range(96):
                if high - low <= quantum:
                    break
                middle = (low + high) / TWO
                if feasible(middle):
                    low = middle
                else:
                    high = middle
            amount = self._round_qty(low, qty_decimals)
        if amount <= ZERO or not feasible(amount):
            return self._no_trade_result(state, mid_price, liquidity_capped)
        execution_quote = quote(amount)
        assert execution_quote is not None
        (
            executed_price,
            fee_cost,
            slippage_cost,
            crossing_cost,
            equity_after_trade,
            new_position,
        ) = execution_quote
        delta = direction * amount
        trade_notional = amount * executed_price
        if trade_notional < self._min_notional or amount < self._min_qty:
            return self._no_trade_result(state, mid_price, liquidity_capped)
        # This is an explicit invariant guard, independent of solver convergence.
        if abs(delta) > abs(requested_delta) or trade_notional > liquidity_cap:
            raise ArithmeticError(
                "execution projection exceeded admitted quantity/liquidity"
            )
        final_limit = self._max_lev if new_position >= ZERO else self._max_short_lev
        risk_reduction_only = abs(new_position) * max(
            mid_price, executed_price
        ) * self._margin_multiplier > equity_after_trade * final_limit or (
            new_position < ZERO
            and abs(new_position) > self._enforce_short_rules(abs(new_position))
        )
        prev_position = state.position

        # 6. Update Average Entry Price (WAP) - PART 2 (Finalized)
        prev_avg_price = state.average_entry_price
        new_avg_price = prev_avg_price

        # Logic for Average Entry Price updates
        if prev_position == ZERO:
            # Opening new position
            if new_position != ZERO:
                new_avg_price = executed_price
            else:
                new_avg_price = ZERO
        elif (new_position > 0 and prev_position < 0) or (
            new_position < 0 and prev_position > 0
        ):
            # Position Flip (Crossed Zero)
            # The closing portion is realized at old avg (implied), new portion opens at executed_price
            new_avg_price = executed_price
        elif abs(new_position) > abs(prev_position):
            # Increasing Position Size (Weighted Average)
            cost_old = abs(prev_position) * prev_avg_price
            cost_new = abs(delta) * executed_price
            new_avg_price = (cost_old + cost_new) / abs(new_position)
        else:
            # Decreasing Position Size or No Change
            # Average entry price remains the same for the remaining portion
            if new_position == ZERO:
                new_avg_price = ZERO

        borrowed_base = state.borrowed_base
        borrowed_quote = state.borrowed_quote

        if new_position >= 0:
            borrowed_base = ZERO
            gross_exposure = abs(new_position) * mid_price
            borrowed_quote = max(ZERO, gross_exposure - equity_after_trade)
        else:
            # Position already capped above, now calculate borrow based on final position
            borrowed_base = abs(new_position)
            borrowed_quote = ZERO

        next_state = ExecutionState(
            position=new_position,
            equity=equity_after_trade,
            borrowed_base=borrowed_base,
            borrowed_quote=borrowed_quote,
            average_entry_price=new_avg_price,
        )

        return ExecutionResult(
            state=next_state,
            delta_units=delta,  # This now accurately reflects the executed delta
            executed_price=executed_price,
            fee_cost=fee_cost,
            slippage_cost=slippage_cost,
            crossing_cost=crossing_cost,
            liquidity_capped=liquidity_capped,
            risk_reduction_only=risk_reduction_only,
        )

    def _no_trade_result(
        self, state: ExecutionState, price: Decimal, capped: bool = False
    ) -> ExecutionResult:
        return ExecutionResult(
            state=state,
            delta_units=ZERO,
            executed_price=price,
            fee_cost=ZERO,
            slippage_cost=ZERO,
            crossing_cost=ZERO,
            liquidity_capped=capped,
        )

    def _depth_fill(
        self, delta: Decimal, book: LimitOrderBook
    ) -> tuple[FillResult | None, bool]:
        """Walk the opposite side of ``book`` for ``delta``.

        Returns ``(fill_or_none, partially_filled)``.  ``None`` means the side
        carries no resting liquidity at all; a returned fill may still be
        partial (``fully_filled=False``), which the caller reports as
        ``liquidity_capped``.  Levels are validated up front — a non-positive
        price or quantity is a malformed feed and raises ``ValueError``
        instead of degrading into a synthetic refusal.
        """
        side = "buy" if delta > 0 else "sell"
        levels = book.asks if side == "buy" else book.bids
        for level in levels:
            if (
                not level.price.is_finite()
                or not level.quantity.is_finite()
                or level.price <= ZERO
                or level.quantity <= ZERO
            ):
                raise ValueError("book levels must carry positive price and quantity")
        if book_total_liquidity(levels) <= ZERO:
            return None, True
        result = market_fill(book, side=side, quantity=abs(delta))
        return result, not result.fully_filled

    def _liquidity_cap(self, delta: Decimal, market: MarketSnapshot) -> Decimal:
        venue_cap = (
            Decimal(str(market.buy_cap_quote))
            if delta > 0
            else Decimal(str(market.sell_cap_quote))
        )
        config_cap = Decimal(str(self.cfg.liquidity.cap_for(self.cfg.symbol)))
        return max(ZERO, min(venue_cap, config_cap))

    def _enforce_short_rules(self, target_abs_units: Decimal) -> Decimal:
        # Rule 1: Globally disabled
        if not self._shorts_enabled:
            return ZERO

        # Rule 2: Symbol not in whitelist
        if self._base_symbol not in self._allowed_short_symbols:
            return ZERO

        # Rule 3: Position exceeds borrow cap
        # If borrow cap is effectively zero (and not set explicitly to infinite), we block shorting.
        if self._short_cap <= EPSILON:
            return ZERO

        if target_abs_units > self._short_cap + EPSILON:
            return self._short_cap

        return target_abs_units

    @staticmethod
    def _round_qty(quantity: Decimal, decimals: int) -> Decimal:
        return quantity.quantize(Decimal(f"1e-{decimals}"), rounding=ROUND_DOWN)

    @staticmethod
    def _round_price(price: Decimal, decimals: int) -> Decimal:
        return price.quantize(Decimal(f"1e-{decimals}"), rounding=ROUND_HALF_UP)
