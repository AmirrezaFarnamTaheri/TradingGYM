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
    replaces both the venue and configured notional caps.
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

    @property
    def total_cost(self) -> Decimal:
        return self.fee_cost + self.slippage_cost + self.crossing_cost


class ExecutionEngine:
    """
    Shared execution logic using pure Decimal arithmetic.

    Market Impact Model
    ===================

    This engine implements a two-component market impact model:

    1. **Volatility Impact (Kyle-style):**
       slip_vol = volatility_lambda * |trade_size| * volatility

       Based on Kyle (1985), market impact is proportional to trade size
       and increases with volatility. The volatility_lambda parameter
       represents the market's sensitivity to informed trading.

    2. **Liquidity Impact (Square-root model):**
       slip_liq = liquidity_lambda * sqrt(notional / liquidity_cap)

       Based on Almgren & Chriss (2001), market impact follows a square-root
       relationship with trade size relative to available liquidity.
       This captures the concave nature of price impact.

    **Total Slippage:**
       slippage_cost = |trade_size| * price * (slip_vol + slip_liq)

    The additive combination assumes the two effects are approximately
    independent. In practice, they interact, but this simplification
    provides a tractable and calibratable model.

    **Calibration Notes:**
    - volatility_lambda: Typically 0.05-0.20 for liquid crypto assets
    - liquidity_lambda: Typically 0.05-0.15 depending on market depth
    - These should be calibrated to historical fill data when available

    **Depth-walk mode:**
       When ``MarketSnapshot.depth_book`` is supplied, the executed price is
       the quantity-weighted average of walking the opposite side of the book
       (`execution/fill_models.market_fill`), the fill is capped to real
       resting liquidity (``liquidity_capped`` flags partial walks, an empty
       side refuses the trade), and ``slippage_cost`` reports the realized
       depth shortfall versus mid (``crossing_cost`` is zero — the book already
       embeds the spread).  Without a book the parametric model above applies
       unchanged.

    References:
    - Kyle, A. S. (1985). "Continuous Auctions and Insider Trading"
    - Almgren, R., & Chriss, N. (2001). "Optimal Execution of Portfolio Transactions"
    - Gatheral, J. (2010). "No-Dynamic-Arbitrage and Market Impact"
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

        # 2. Boundary Conversion: Float Inputs -> Decimal
        target = Decimal(str(target_position))
        mid_price = Decimal(str(market.mid_price))

        # Fix: Dust Handling (prevent rounding errors from leaving tiny positions)
        if abs(target) < self._min_qty:
            target = ZERO

        qty_decimals = self._decimals.qty_for(self.cfg.symbol)
        price_decimals = self._decimals.price_for(self.cfg.symbol)

        delta = target - Decimal(str(state.position))
        # A true flatten request is an accounting boundary, not a target rounded
        # away from zero.  Preserve an exact close even when the incoming state
        # contains sub-precision residue from an older float-backed path.
        if target != ZERO:
            delta = self._round_qty(delta, qty_decimals)

        # No trade needed?
        if abs(delta) == ZERO:
            return self._no_trade_result(state, mid_price, False)

        new_position = Decimal(str(state.position)) + delta

        book_fill: FillResult | None = None
        liquidity_capped = False
        if market.depth_book is not None:
            # 2a. Depth-walk mode: fill against walked L2 liquidity instead of
            # the parametric cap/impact model.  A side with no resting
            # liquidity refuses the trade rather than synthesizing a fill.
            book_fill, liquidity_capped = self._depth_fill(delta, market.depth_book)
            if book_fill is None:
                return self._no_trade_result(state, mid_price, True)
            signed_fill = (
                book_fill.filled_quantity if delta > 0 else -book_fill.filled_quantity
            )
            delta = self._round_qty(signed_fill, qty_decimals)
            new_position = state.position + delta
        else:
            liquidity_cap = self._liquidity_cap(delta, market)
        trade_notional = abs(delta) * mid_price

        # 2. Liquidity Capping (parametric path only; depth walk already
        # bounded the fill to real resting quantity).
        if book_fill is None and trade_notional > liquidity_cap:
            scale = liquidity_cap / max(trade_notional, EPSILON)
            delta = self._round_qty(delta * scale, qty_decimals)
            new_position = state.position + delta
            trade_notional = abs(delta) * mid_price
            liquidity_capped = True
            # Log at DEBUG to avoid spam
            logger.debug("Trade capped: %s -> %s", target, new_position)

        # 3. Minimum Size Checks
        if trade_notional < self._min_notional or abs(delta) < self._min_qty:
            return self._no_trade_result(state, mid_price, liquidity_capped)

        # 4. Pricing & Costs
        spread_dec = Decimal(str(market.spread_bps))
        half_spread = (spread_dec / BP_DIVISOR) * mid_price / TWO
        if book_fill is not None:
            executed_price = self._round_price(book_fill.average_price, price_decimals)
        else:
            executed_price = mid_price + (half_spread if delta > 0 else -half_spread)
            executed_price = self._round_price(executed_price, price_decimals)

        if not executed_price.is_finite() or executed_price <= ZERO:
            # A valid sub-tick price can round to zero even when the incoming
            # book and mid are positive; never settle a zero-price fill.
            raise ValueError("computed execution price must be positive")

        vol_hint = Decimal(str(max(market.volatility_hint, 0)))

        def _costs_for(
            d: Decimal, px: Decimal, notional: Decimal
        ) -> tuple[Decimal, Decimal, Decimal]:
            fee = abs(d) * px * self._taker_fee
            if book_fill is not None:
                # The walked average price already embeds spread crossing and
                # depth impact; layering the parametric models on top would
                # double-count microstructure costs.
                slippage = abs(d) * abs(px - mid_price)
                crossing = ZERO
            else:
                slip_vol = self._vol_lambda * abs(d) * vol_hint
                slip_liq = (
                    self._liq_lambda
                    * (max(notional, ZERO) / max(liquidity_cap, EPSILON)).sqrt()
                )
                slippage = abs(d) * px * (slip_vol + slip_liq)
                crossing = abs(d) * half_spread
            return fee, slippage, crossing

        fee_cost, slippage_cost, crossing_cost = _costs_for(
            delta, executed_price, trade_notional
        )

        equity_after_trade = state.equity - fee_cost - slippage_cost - crossing_cost

        # 5. Leverage Checks (Decimal Math)
        # Determine applicable leverage limit
        if new_position >= 0:
            limit = self._max_lev
        else:
            limit = self._max_short_lev

        # Max Units = (Equity * Limit) / (Price * MarginMultiplier)
        margin_units_divisor = max(executed_price, EPSILON) * self._margin_multiplier
        max_units = (max(equity_after_trade, EPSILON) * limit) / margin_units_divisor

        if abs(new_position) > max_units + EPSILON:
            capped_target = Decimal(int(np.sign(float(new_position)))) * max_units
            delta = self._round_qty(capped_target - state.position, qty_decimals)
            new_position = state.position + delta
            trade_notional = abs(delta) * executed_price

            # Recalculate costs for clipped delta
            fee_cost, slippage_cost, crossing_cost = _costs_for(
                delta, executed_price, trade_notional
            )

            equity_after_trade = state.equity - fee_cost - slippage_cost - crossing_cost

        # 6. Update Average Entry Price (WAP) - PART 1 (Before short caps)
        # We need to know if we are closing or opening to update WAP correctly,
        # but the final position might change due to short caps below.
        # However, usually short caps just limit the size.

        prev_position = state.position

        # 7. Update Borrow State & Short Caps
        # CRITICAL FIX: Apply short rules BEFORE finalizing position
        if new_position < 0:
            # Enforce short selling rules (caps, whitelisting)
            capped_short_units = self._enforce_short_rules(abs(new_position))

            # If capped, we must adjust delta and costs again!
            if capped_short_units != abs(new_position):
                new_position = -capped_short_units
                delta = new_position - state.position
                delta = self._round_qty(delta, qty_decimals)

                # Recalc costs for short-capped delta
                trade_notional = abs(delta) * executed_price
                fee_cost, slippage_cost, crossing_cost = _costs_for(
                    delta, executed_price, trade_notional
                )
                equity_after_trade = (
                    state.equity - fee_cost - slippage_cost - crossing_cost
                )

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
            new_avg_price = self._round_price(new_avg_price, price_decimals)
        else:
            # Decreasing Position Size or No Change
            # Average entry price remains the same for the remaining portion
            if new_position == ZERO:
                new_avg_price = ZERO

        borrowed_base = state.borrowed_base
        borrowed_quote = state.borrowed_quote

        if new_position >= 0:
            borrowed_base = ZERO
            gross_exposure = abs(new_position) * executed_price
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
            if level.price <= ZERO or level.quantity <= ZERO:
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
        return max(EPSILON, min(venue_cap, config_cap))

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
