"""
Live Execution Adapter
======================
Translates RL 'Target Position' into specific Orders.
Handles state reconciliation and EMERGENCY FLATTENING.
"""

import logging
import asyncio
import json
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Dict, Any

from trading_gym.common.config import AppConfig
from trading_gym.adapters.enhanced_aban_client import (
    EnhancedAbanClient,
    OrderStatus,
)
from trading_gym.adapters.aban_transport import AbanTransport
from trading_gym.production.mutation_retry import (
    MutationOutcomeUnknown,
    MutationOutbox,
    MutationRetryPolicy,
    execute_mutation_with_retry,
    new_mutation_idempotency_key,
    scan_outbox,
)
from trading_gym.production.pre_trade_gate import evaluate_pre_trade_gate
from trading_gym.production.runtime import (
    LiveExecutionCoordinator,
    intent_sha256,
)
from trading_gym.production.emergency import EmergencyController
from trading_gym.production.venues import AbanTetherVenue, VenueOrderIntent
from trading_gym.treasury.reservations import ReservationLedger
from trading_gym.venue.asset_registry import is_shortable
from axiomforge.live_trading import LiveConsent

logger = logging.getLogger(__name__)

#: Failures that may be retried automatically under a stable idempotency key:
#: provably pre-send network errors, plus unknown-outcome errors whose replay
#: safety is then arbitrated by the coordinator (venue replay capability).
#: Anything else (validation, auth, consent, emergency halt) aborts.
DEFAULT_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    MutationOutcomeUnknown,
)
try:  # pragma: no cover - exercised only where requests is installed
    from requests import exceptions as _requests_exceptions

    DEFAULT_RETRYABLE_EXCEPTIONS = (
        MutationOutcomeUnknown,
        _requests_exceptions.ConnectTimeout,
    )
except Exception:  # pragma: no cover - requests is a hard dependency anyway
    pass


class LiveExecutionAdapter:
    """Translate target positions while delegating every mutation to production authority."""

    def __init__(
        self,
        config: AppConfig,
        api_token: str,
        *,
        live_consent: LiveConsent | None = None,
        proposal_id: str | None = None,
        mutation_policy: MutationRetryPolicy | None = None,
        mutation_outbox: MutationOutbox | None = None,
    ):
        self.cfg = config
        policy_path = (
            Path(__file__).resolve().parents[1] / "adapters" / "aban_tether_policy.json"
        )
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.api = EnhancedAbanClient(
            api_token,
            rate_limit_per_minute=int(policy["request_rate_per_minute"]),
        )
        self._live_consent = live_consent
        self._proposal_id = proposal_id
        self._mutation_policy = mutation_policy or MutationRetryPolicy(
            max_attempts=3,
            retryable_exceptions=DEFAULT_RETRYABLE_EXCEPTIONS,
        )
        self._mutation_outbox = mutation_outbox or MutationOutbox()
        self._coordinator = LiveExecutionCoordinator(
            {"abantether": AbanTetherVenue(AbanTransport(self.api))},
            emergency_controller=EmergencyController(),
        )
        self.symbol = config.env.symbol
        # Base asset e.g. "BTC", "ETH"
        self.base_asset = self.symbol.replace(config.env.quote_ccy, "")
        # Quote asset e.g. "IRT", "USDT"
        self.quote_asset = config.env.quote_ccy

        # Cache decimals for rounding
        # Use Decimal for all internal constants
        self.qty_decimals = self.cfg.env.decimals.qty_for(self.symbol)
        self.price_decimals = self.cfg.env.decimals.price_for(self.symbol)
        self.min_qty = Decimal(str(self.cfg.env.decimals.min_qty_for(self.symbol)))

        # Concurrency Lock to prevent race conditions during rapid reconcile calls
        self._reconcile_lock = asyncio.Lock()

        # Last flatten failure detail (empty when flatten has not failed);
        # flatten() returns False with no context otherwise.
        self._last_flatten_error = ""

        # Kraken-style balance reservations: spending mutations lock
        # cost×(1+fee) up front so pending/ambiguous orders cannot
        # double-commit the same funds across reconciles.  Built lazily from
        # live balances on first use.
        self._reservations: ReservationLedger | None = None

        logger.info(
            f"LiveAdapter Initialized: {self.symbol} (Base: {self.base_asset}, Quote: {self.quote_asset})"
        )

    def _ensure_reservation_ledger(self) -> ReservationLedger:
        if self._reservations is None:
            opening: dict[str, Decimal] = {}
            for b in self.api.get_balance():
                # The SDK distinguishes available balance from venue-blocked
                # funds. Counting `blocked` as spendable permits new orders to
                # commit funds already held by the venue.
                available = b.balance
                if not available.is_finite() or available < 0:
                    raise ValueError("invalid_available_broker_balance")
                currency = b.symbol.upper()
                if currency in opening:
                    raise ValueError("duplicate_broker_balance_currency")
                opening[currency] = available
            self._reservations = ReservationLedger(
                opening or {self.quote_asset.upper(): Decimal("0")}
            )
        return self._reservations

    def _taker_fee_rate(self) -> Decimal:
        """Reject missing/invalid fee assumptions rather than under-reserving."""
        try:
            bps = Decimal(str(self.cfg.env.cost.taker_fee_bps))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid_taker_fee_configuration") from exc
        if not bps.is_finite() or bps < 0:
            raise ValueError("invalid_taker_fee_configuration")
        return bps / Decimal("10000")

    def _quote_cost(self, quantity: Decimal, reference_price: float) -> Decimal:
        """Conservatively round the estimated spend up when reserving funds."""
        qty = Decimal(str(quantity))
        price = Decimal(str(reference_price))
        if not qty.is_finite() or qty <= 0 or not price.is_finite() or price <= 0:
            raise ValueError("invalid_quote_reservation_inputs")
        rate = Decimal("1") + self._taker_fee_rate()
        return (qty * price * rate).quantize(Decimal("1e-8"), rounding=ROUND_UP)

    async def _call_api(self, func, *args, **kwargs):
        """Run read-only SDK calls in a thread to avoid blocking the event loop."""
        return await asyncio.to_thread(func, *args, **kwargs)

    async def _mutate(
        self,
        operation: str,
        *,
        side: str,
        quantity: Decimal,
        reference_id: str = "",
        reserve_currency: str | None = None,
        reserve_amount: Decimal | None = None,
        require_flat_verification: bool = False,
    ):
        """Submit one payload-bound mutation through the sole production coordinator.

        The mutation is retried automatically on transient network failures.
        Every attempt reuses ONE idempotency key, so a retry after an
        ambiguous failure observes the original receipt from the coordinator
        instead of duplicating the venue order.  Each attempt is journalled to
        the durable mutation outbox for post-hoc reconciliation.

        When ``reserve_currency``/``reserve_amount`` are supplied (spending
        mutations), the funds are locked in the reservation ledger before any
        journal write or venue contact; an over-committed request aborts
        locally with :class:`ReservationError`.  On a committed outcome the
        reservation is settled, on a terminal failure it is released, and on
        an ambiguous outcome it stays held until reconciliation resolves the
        order's fate — mirroring the coordinator's ambiguous-key hold.
        """
        if self._live_consent is None or not self._proposal_id:
            raise PermissionError("governed_live_consent_required")

        # The previous process may have lost the venue acknowledgement. A
        # fresh idempotency key cannot repair that uncertainty and can create
        # a second order. Block until an operator has reconciled the outbox.
        if operation != "order.cancel" and scan_outbox(self._mutation_outbox):
            # Risk-reducing cancellation of a known order remains available;
            # new fills cannot proceed until prior unknown fills are resolved.
            raise PermissionError("unresolved_mutations_require_reconciliation")

        idempotency_key = new_mutation_idempotency_key()
        proposal_id = self._proposal_id

        # Advisory pre-trade gate: cheap local checks run before any journal
        # write or venue contact. A denial never reaches the coordinator.
        gate = evaluate_pre_trade_gate(
            operation=operation,
            quantity=Decimal(str(quantity)),
            idempotency_key=idempotency_key,
            reference_id=reference_id,
        )
        if not gate.allowed:
            raise PermissionError(
                f"pre_trade_gate_denied:{','.join(gate.denial_reasons)}"
            )

        # Kraken lib.rs:585-592: lock the spendable cost before mutating.
        did_reserve = False
        if reserve_currency is not None and reserve_amount is not None:
            self._ensure_reservation_ledger().reserve(
                order_id=idempotency_key,
                currency=reserve_currency,
                amount=Decimal(str(reserve_amount)),
            )
            did_reserve = True

        def build_intent() -> VenueOrderIntent:
            return VenueOrderIntent(
                venue="abantether",
                operation=operation,
                base_symbol=self.base_asset,
                quote_symbol=self.quote_asset,
                side=side,
                quantity=Decimal(str(quantity)),
                reference_id=reference_id,
                idempotency_key=idempotency_key,
            )

        payload_digest = intent_sha256(build_intent())
        outbox = self._mutation_outbox

        def submit():
            intent = build_intent()
            authorization = self._coordinator.bind_authorization(
                intent,
                consent=self._live_consent,
                proposal_id=proposal_id,
            )
            receipt = self._coordinator.execute(
                intent,
                authorization,
                consent=self._live_consent,
            )
            result = receipt.get("result") if isinstance(receipt, dict) else None
            status = getattr(result, "status", None)
            if status is not None:
                if status in {
                    OrderStatus.REJECTED,
                    OrderStatus.FAILED,
                    OrderStatus.CANCELED,
                }:
                    if operation != "order.cancel" or status != OrderStatus.CANCELED:
                        raise ValueError("venue_order_rejected")
                elif status != OrderStatus.FILLED:
                    # An acknowledged, possibly partially filled order must
                    # retain its reservation until authoritative reconciliation.
                    raise MutationOutcomeUnknown("venue_order_awaiting_fill")
            elif did_reserve:
                # A non-typed response cannot prove settlement. Fail closed.
                raise MutationOutcomeUnknown("venue_fill_status_unavailable")
            return receipt

        def journal_pending() -> None:
            try:
                outbox.claim_pending(
                    idempotency_key=idempotency_key,
                    proposal_id=proposal_id,
                    operation=operation,
                    payload_sha256=payload_digest,
                )
            except Exception:
                # The submit callback cannot run if the intent was not
                # recorded; release the pre-send reservation instead.
                if did_reserve:
                    self._ensure_reservation_ledger().release(order_id=idempotency_key)
                raise

        def journal_outcome(outcome: str, exc: BaseException | None) -> None:
            try:
                outbox.record_outcome(
                    idempotency_key=idempotency_key,
                    proposal_id=proposal_id,
                    operation=operation,
                    payload_sha256=payload_digest,
                    outcome=outcome,
                    detail="" if exc is None else type(exc).__name__,
                )
            except Exception:  # noqa: BLE001 - journaling must never mask trading
                logger.warning(
                    "mutation outbox write failed error_type=%s", "journal_error"
                )

        def on_committed(receipt: object) -> None:
            # Commit the reservation before publishing the durable "committed"
            # marker. A settlement failure leaves the outbox pending/ambiguous
            # instead of declaring success and making the funds spendable.
            if did_reserve:
                self._ensure_reservation_ledger().commit_settlement(
                    order_id=idempotency_key,
                    currency=reserve_currency,  # type: ignore[arg-type]
                    settled_amount=Decimal(str(reserve_amount)),
                )
            outbox.record_outcome(
                idempotency_key=idempotency_key,
                proposal_id=proposal_id,
                operation=operation,
                payload_sha256=payload_digest,
                # Emergency closes remain unresolved until a separate venue
                # snapshot confirms the gross portfolio is flat. Persist the
                # hold before returning to flatten(), including across a crash.
                outcome="ambiguous" if require_flat_verification else "committed",
                detail="flatten_unverified" if require_flat_verification else "",
            )

        def on_ambiguous(exc: BaseException) -> None:
            journal_outcome("ambiguous", exc)
            # Reservation intentionally held: the order may exist venue-side.

        def on_terminal(exc: BaseException) -> None:
            journal_outcome("terminal", exc)
            if did_reserve:
                try:
                    self._ensure_reservation_ledger().release(order_id=idempotency_key)
                except Exception:  # noqa: BLE001 - bookkeeping never masks trading
                    logger.warning(
                        "reservation release failed error_type=%s", "ledger_error"
                    )

        policy = self._mutation_policy
        outcome = await asyncio.to_thread(
            execute_mutation_with_retry,
            submit,
            policy=policy,
            on_pending=journal_pending,
            on_committed=on_committed,
            on_ambiguous=on_ambiguous,
            on_terminal=on_terminal,
            allow_unknown_replay=False,  # Aban venue has no verified replay deduplication.
        )
        return outcome.receipt["result"]

    async def reconcile(
        self, target_position: float, current_price: float
    ) -> Dict[str, Any]:
        """
        The Core Logic:
        1. Fetch current Real Position (Net of Spot and Short)
        2. Calculate Delta (Target - Current)
        3. Execute Order (Spot Buy/Sell, Short Create/Close)
        """
        if self._reconcile_lock.locked():
            logger.warning(
                "Reconcile called while locked! Skipping to prevent race condition."
            )
            return {"status": "skipped", "reason": "locked"}

        async with self._reconcile_lock:
            try:
                # A stale or malformed price cannot authorize *any* trade,
                # including sells and shorts that do not reserve quote cash.
                price_dec = Decimal(str(current_price))
                target_dec = Decimal(str(target_position))
                if not price_dec.is_finite() or price_dec <= 0:
                    raise ValueError("invalid_reconciliation_price")
                if not target_dec.is_finite():
                    raise ValueError("invalid_target_position")

                # 1. Get Current Real Position (Decimal)
                current_pos = await asyncio.to_thread(self._get_net_position)

                delta = target_dec - current_pos

                # Round delta to allowed precision to avoid API errors
                delta = delta.quantize(
                    Decimal(f"1e-{self.qty_decimals}"), rounding=ROUND_DOWN
                )

                if abs(delta) < self.min_qty:
                    return {
                        "status": "skipped",
                        "reason": "dust",
                        "delta": float(delta),
                    }

                side = "buy" if delta > 0 else "sell"
                qty = abs(delta)

                # A forbidden short leg must be rejected before selling any
                # existing spot holdings during a long-to-short reversal.
                if delta < 0 and target_dec < 0 and not is_shortable(self.symbol):
                    return {"status": "skipped", "reason": "short_not_allowed"}

                logger.info(
                    f"LIVE EXECUTION: {side.upper()} {qty} {self.symbol} @ {current_price}"
                )

                # 2. Determine Execution Strategy
                order_id = None

                if side == "buy":
                    # Buying:
                    # If we have short position (negative net pos), we must close it first.
                    if current_pos < 0:
                        # We are closing a short position
                        short_positions = await self._call_api(
                            self.api.get_short_positions
                        )
                        # Filter for current symbol
                        my_shorts = [
                            p for p in short_positions if p.get("symbol") == self.symbol
                        ]

                        # Logic: cover min(buy_qty, abs(current_short_pos))
                        abs_current_pos = abs(current_pos)
                        qty_to_cover = min(qty, abs_current_pos)
                        remaining_buy = qty - qty_to_cover

                        # Validate venue inventory *before* any mutation.
                        # Otherwise a missing short leg silently turns a
                        # reversal into an additional, unintended spot buy.
                        short_ids: set[str] = set()
                        available_to_cover = Decimal("0")
                        for short_pos in my_shorts:
                            pid = str(short_pos["id"])
                            amount = Decimal(str(short_pos["amount"]))
                            if (
                                pid in short_ids
                                or not amount.is_finite()
                                or amount <= 0
                            ):
                                raise ValueError("invalid_short_position_inventory")
                            short_ids.add(pid)
                            available_to_cover += amount
                        if available_to_cover < qty_to_cover:
                            raise ValueError("insufficient_short_position_inventory")

                        # Close multiple short positions in FIFO order.
                        # Close positions in FIFO order until qty_to_cover is satisfied
                        if my_shorts and qty_to_cover > Decimal("0"):
                            remaining_to_cover = qty_to_cover
                            for short_pos in my_shorts:
                                if remaining_to_cover <= Decimal("0"):
                                    break

                                pid = str(short_pos["id"])
                                short_amount = Decimal(str(short_pos.get("amount", 0)))

                                # Close either the full position or remaining amount needed
                                close_amount = min(short_amount, remaining_to_cover)
                                await self._mutate(
                                    "short.close",
                                    side="buy",
                                    quantity=close_amount,
                                    reference_id=pid,
                                    reserve_currency=self.quote_asset,
                                    reserve_amount=self._quote_cost(
                                        close_amount, current_price
                                    ),
                                )
                                logger.info(f"Closed Short {pid} amount {close_amount}")
                                remaining_to_cover -= close_amount
                                order_id = f"close_short_{pid}"

                            if remaining_to_cover > Decimal("0"):
                                raise RuntimeError("short_coverage_incomplete")

                        if remaining_buy >= self.min_qty:
                            # Buy remaining on spot
                            resp = await self._mutate(
                                "spot.market",
                                side="buy",
                                quantity=remaining_buy,
                                reserve_currency=self.quote_asset,
                                reserve_amount=self._quote_cost(
                                    remaining_buy, current_price
                                ),
                            )
                            order_id = resp.id
                    else:
                        # Pure Spot Buy
                        resp = await self._mutate(
                            "spot.market",
                            side="buy",
                            quantity=qty,
                            reserve_currency=self.quote_asset,
                            reserve_amount=self._quote_cost(qty, current_price),
                        )
                        order_id = resp.id

                else:
                    # Selling:
                    # If current_pos > 0, we are selling spot.
                    if current_pos > 0:
                        qty_to_sell = min(qty, current_pos)
                        remaining_short = qty - qty_to_sell

                        if qty_to_sell >= self.min_qty:
                            resp = await self._mutate(
                                "spot.market",
                                side="sell",
                                quantity=qty_to_sell,
                                reserve_currency=self.base_asset,
                                reserve_amount=qty_to_sell,
                            )
                            order_id = resp.id

                        if remaining_short >= self.min_qty:
                            resp = await self._mutate(
                                "short.open",
                                side="sell",
                                quantity=remaining_short,
                                reserve_currency=self.base_asset,
                                reserve_amount=remaining_short,
                            )
                            order_id = resp.id
                    else:
                        # Pure Short Sell (eligibility checked before mutation)
                        resp = await self._mutate(
                            "short.open",
                            side="sell",
                            quantity=qty,
                            reserve_currency=self.base_asset,
                            reserve_amount=qty,
                        )
                        order_id = resp.id

                if order_id is None:
                    return {"status": "skipped", "reason": "no_executable_leg"}
                return {
                    "status": "executed",
                    "order_id": order_id,
                    "side": side,
                    "qty": float(qty),
                }
            except ValueError as exc:
                logger.error("Reconciliation validation failed: %s", exc)
                return {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            except Exception as exc:
                logger.error("Reconciliation failed: %s", exc)
                return {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

    async def _verify_broker_flat(self) -> None:
        """Require positive evidence that gross exposure and open orders are gone.

        A zero *net* position can hide an offsetting spot holding and short.
        A venue fill receipt alone also does not prove an updated account
        snapshot. Neither condition may be reported as a completed flatten.
        """
        balances = await self._call_api(self.api.get_balance)
        shorts = await self._call_api(self.api.get_short_positions)
        orders = await self._call_api(
            self.api.get_orders,
            base_symbol=self.base_asset,
            quote_symbol=self.quote_asset,
            status=[OrderStatus.NEW, OrderStatus.OPEN, OrderStatus.PROCESSING],
        )
        if not isinstance(balances, list) or not isinstance(shorts, list):
            raise RuntimeError("broker_position_snapshot_unavailable")
        if not isinstance(orders, dict) or not isinstance(orders.get("data"), list):
            raise RuntimeError("broker_order_snapshot_unavailable")
        for balance in balances:
            if balance.symbol == self.base_asset:
                gross = Decimal(str(balance.balance)) + Decimal(str(balance.blocked))
                if not gross.is_finite() or gross < 0:
                    raise ValueError("invalid_broker_spot_balance")
                if gross >= self.min_qty:
                    raise RuntimeError("gross_exposure_not_cleared")
        for short in shorts:
            if short.get("symbol") == self.symbol:
                amount = Decimal(str(short["amount"]))
                if not amount.is_finite() or amount < 0:
                    raise ValueError("invalid_broker_short_balance")
                if amount >= self.min_qty:
                    raise RuntimeError("gross_exposure_not_cleared")
        if orders["data"]:
            raise RuntimeError("open_orders_not_cleared")

    async def flatten(self) -> bool:
        """Serialize emergency closing with ordinary reconciliation mutations."""
        async with self._reconcile_lock:
            return await self._flatten_locked()

    async def _flatten_locked(self) -> bool:
        """Attempt emergency reduction, reporting success only after verification."""
        self._last_flatten_error = ""
        logger.critical("🚨 EXECUTING EMERGENCY FLATTEN PROTOCOL")
        try:
            previous = self._mutation_outbox.latest_by_key()
            if any(
                entry["status"] in {"pending", "ambiguous"}
                for entry in previous.values()
            ):
                raise PermissionError("unresolved_mutations_require_reconciliation")

            # 1. Cancel open orders first
            open_orders_resp = await self._call_api(
                self.api.get_orders,
                base_symbol=self.base_asset,
                quote_symbol=self.quote_asset,
                status=[
                    OrderStatus.NEW,
                    OrderStatus.OPEN,
                    OrderStatus.PROCESSING,
                ],  # type: ignore
            )
            # get_orders returns Dict, keys: total, data (list)
            open_orders = open_orders_resp.get("data", [])

            for order in open_orders:
                await self._mutate(
                    "order.cancel",
                    side="none",
                    quantity=Decimal("0"),
                    reference_id=str(order["id"]),
                )

            # 2. Get current net position
            current_pos = await asyncio.to_thread(self._get_net_position)

            if abs(current_pos) >= self.min_qty:
                if current_pos > 0:
                    await self._mutate(
                        "spot.market",
                        side="sell",
                        quantity=current_pos,
                        reserve_currency=self.base_asset,
                        reserve_amount=current_pos,
                        require_flat_verification=True,
                    )
                else:
                    short_positions = await self._call_api(
                        self.api.get_short_positions
                    )
                    my_shorts = [
                        p for p in short_positions if p.get("symbol") == self.symbol
                    ]
                    if not my_shorts:
                        raise RuntimeError("short_positions_missing_for_flatten")
                    for short in my_shorts:
                        await self._mutate(
                            "short.close",
                            side="buy",
                            quantity=Decimal("0"),
                            reference_id=str(short["id"]),
                            require_flat_verification=True,
                        )

            # The outbox keeps each closing fill ambiguous until this
            # independent snapshot succeeds. A crash at any earlier point
            # leaves pending/ambiguous evidence that blocks another order.
            await self._verify_broker_flat()
            for key, entry in self._mutation_outbox.latest_by_key().items():
                if key not in previous and entry["status"] == "ambiguous":
                    self._mutation_outbox.record_outcome(
                        idempotency_key=key,
                        proposal_id=entry["proposal_id"],
                        operation=entry["operation"],
                        payload_sha256=entry["payload_sha256"],
                        outcome="committed",
                        detail="flatten_verified",
                    )
            logger.critical("Emergency flatten verified by broker position snapshot")
            return True

        except Exception as exc:
            # str(exc) is built from a controlled vocabulary (gate check names
            # / governance reasons), so it is safe to surface to an operator.
            # Discarding it made a local gate denial look like a venue failure.
            logger.critical("Emergency flatten failed: %s", exc)
            self._last_flatten_error = f"{type(exc).__name__}: {exc}"
            return False

    def get_live_equity(self) -> float:
        """Calculate Total Equity (Collateral + PnL)."""
        try:
            # Sum of all balances in Quote Currency (approx)
            balances = self.api.get_balance()
            total_equity = Decimal("0.0")

            # Get all Tickers for conversion
            tickers_data = self.api.get_ticker()
            if not isinstance(tickers_data, dict):
                # Should theoretically be Dict[str, TickerData] if symbol=None
                logger.warning(
                    "get_ticker did not return a dict of tickers. Assuming empty."
                )
                tickers_data = {}

            # Helper to find ticker
            def get_mid_price(symbol: str) -> Decimal:
                # Logic update: Match against constructed pair (e.g. BTC -> BTCUSDT)
                pair = f"{symbol}{self.quote_asset}"

                # Access by key directly if possible, or search
                # EnhancedAbanClient returns dict keyed by market name (usually pair)
                if pair in tickers_data:
                    return tickers_data[pair].mid_price

                # Fallback search
                for ticker in tickers_data.values():
                    if ticker.symbol == pair:
                        return ticker.mid_price
                return Decimal("0.0")

            # 1. Spot Assets Equity
            for b in balances:
                symbol = b.symbol
                amount = b.balance + b.blocked

                if amount <= 0:
                    continue

                if symbol == self.quote_asset:
                    total_equity += amount
                else:
                    # Convert to Quote
                    mid = get_mid_price(symbol)
                    if not mid.is_finite() or mid <= 0:
                        raise ValueError("missing_or_invalid_asset_valuation")
                    total_equity += amount * mid

            # 2. Short Positions PnL
            shorts = self.api.get_short_positions()
            for s in shorts:
                if s.get("symbol") == self.symbol:
                    # Ensure we handle potential string/float types from raw API dict
                    amt = Decimal(str(s.get("amount", 0)))
                    entry = Decimal(str(s.get("entry_price", 0)))

                    # Get current price for this symbol
                    current_mid = get_mid_price(self.base_asset)

                    if not current_mid.is_finite() or current_mid <= 0:
                        raise ValueError("missing_or_invalid_short_valuation")
                    # PnL = Amount * (Entry Price - Current Price)
                    pnl = amt * (entry - current_mid)
                    total_equity += pnl

            if not total_equity.is_finite() or total_equity <= 0:
                raise RuntimeError("live equity is unavailable or non-positive")
            return float(total_equity)
        except Exception as exc:
            logger.error(
                "Error calculating live equity error_type=%s", type(exc).__name__
            )
            raise RuntimeError("unable to determine live equity") from exc

    def _get_net_position(self) -> Decimal:
        """
        Calculate net position: Spot Balance - Short Obligations
        Returns: Decimal
        """
        # 1. Spot Balance
        balances = self.api.get_balance()
        spot_bal = Decimal("0.0")
        for b in balances:
            if b.symbol == self.base_asset:
                spot_bal = b.balance + b.blocked
                break

        # 2. Short Position
        short_bal = Decimal("0.0")
        shorts = self.api.get_short_positions()
        for s in shorts:
            if s.get("symbol") == self.symbol:
                short_bal += Decimal(str(s.get("amount", 0)))

        return spot_bal - short_bal


class DryRunLiveExecutionAdapter(LiveExecutionAdapter):
    """Read live state but never submit, cancel, close, or create orders."""

    async def reconcile(
        self, target_position: float, current_price: float
    ) -> Dict[str, Any]:
        current = await asyncio.to_thread(self._get_net_position)
        logger.info(
            "DRY RUN: target=%s current=%s price=%s; no order submitted",
            target_position,
            current,
            current_price,
        )
        return {
            "status": "simulated",
            "target_position": float(target_position),
            "current_position": float(current),
            "current_price": float(current_price),
        }

    async def flatten(self) -> bool:
        logger.critical("DRY RUN: panic flatten requested; no live order submitted")
        return True
