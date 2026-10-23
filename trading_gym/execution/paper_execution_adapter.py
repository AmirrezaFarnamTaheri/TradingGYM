"""A durable virtual account on the existing accounting/execution engines.

Market reads may use a venue client. Broker balance/order APIs are never used.
This is a parametric fill simulation, not a venue-fill or liquidation guarantee.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import time

from filelock import FileLock

from trading_gym.accounting.ledger import AccountingConflict, PortfolioLedger
from trading_gym.accounting.models import AccountingEvent, AccountingEventType
from trading_gym.common.config import AppConfig
from trading_gym.common.persistence import atomic_write_json, read_json
from trading_gym.execution.accounting_bridge import (
    execution_accounting_events,
    state_from_account,
)
from trading_gym.execution.execution_engine import ExecutionEngine, MarketSnapshot
from trading_gym.governance.ledger import AuditLedger
from trading_gym.execution.rebalance_policy import admit_rebalance

ZERO = Decimal("0")


class PaperExecutionAdapter:
    owns_simulated_account = True
    can_acknowledge_panic = False
    account_mode = "paper"

    def __init__(
        self,
        config: AppConfig,
        api_token: str = "",
        *,
        account_path: Path,
        initial_equity: Decimal,
        market_client=None,
        account_id: str | None = None,
    ):
        self.cfg = config
        self.symbol = config.env.symbol
        self.quote_asset = config.env.quote_ccy
        self.account_id = account_id or f"paper-{self.symbol.lower()}"
        self.account_path = Path(account_path)
        if self.account_path.is_symlink() or self.account_path.suffix != ".json":
            raise ValueError("paper account must be a regular .json ledger path")
        self.account_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = FileLock(str(self.account_path) + ".paper.lock")
        self.initial_equity = Decimal(str(initial_equity))
        if not self.initial_equity.is_finite() or self.initial_equity <= ZERO:
            raise ValueError("paper initial equity must be finite and positive")
        self.engine = ExecutionEngine(config.env)
        self.daily_rate = Decimal(
            str(config.env.leverage.financing.daily_pct)
        ) / Decimal(100)
        if not self.daily_rate.is_finite() or self.daily_rate < ZERO:
            raise ValueError("paper financing rate must be finite and non-negative")
        self.ledger = PortfolioLedger(
            self.account_id,
            path=self.account_path,
            ledger=AuditLedger(path=self.account_path.with_suffix(".audit.jsonl")),
        )
        manifest_path = self.account_path.with_suffix(".manifest.json")
        recipe = config.env.model_dump(mode="json")
        # A durable breaker path is an operational location, not a cost recipe.
        recipe["breaker"]["state_file"] = None
        digest = hashlib.sha256(
            json.dumps(
                recipe, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        manifest = {
            "schema_version": 1,
            "account_id": self.account_id,
            "mode": "paper",
            "initial_equity": str(self.initial_equity),
            "execution_recipe_sha256": digest,
        }
        with self._lock:
            if self.account_path.exists() and not manifest_path.exists():
                raise AccountingConflict(
                    "existing account lacks a paper execution manifest"
                )
            if manifest_path.exists():
                existing = read_json(manifest_path, None)
                if (
                    not isinstance(existing, dict)
                    or {k: v for k, v in existing.items() if k != "observation_anchor"}
                    != manifest
                ):
                    raise AccountingConflict(
                        "paper account recipe changed; create a new account"
                    )
            else:
                atomic_write_json(manifest_path, manifest)
            self.ledger.create(
                {self.quote_asset: self.initial_equity}, actor="paper-account"
            )
        if market_client is None:
            from trading_gym.adapters.enhanced_aban_client import EnhancedAbanClient

            market_client = EnhancedAbanClient(api_token)
        self.api = market_client
        self._last_mid: Decimal | None = None

    def bind_observation_contract(
        self, *, model_bundle_id: str, reference_price: Decimal
    ) -> dict:
        """Persist the model/account reference once and reuse it across restarts.

        The supplied price is used only for a new, untouched account. An active
        legacy account cannot silently choose a new observation origin. Changing
        model identity requires an explicitly migrated or new paper account.
        """
        if (
            not isinstance(model_bundle_id, str)
            or not model_bundle_id
            or len(model_bundle_id) > 128
        ):
            raise ValueError(
                "paper observation contract requires a bounded model identity"
            )
        price = Decimal(str(reference_price))
        if not price.is_finite() or price <= ZERO:
            raise ValueError("paper reference price must be finite and positive")
        path = self.account_path.with_suffix(".manifest.json")
        with self._lock:
            manifest = read_json(path, None)
            if not isinstance(manifest, dict):
                raise AccountingConflict("paper execution manifest is missing")
            anchor = manifest.get("observation_anchor")
            if anchor is None:
                if self.ledger.snapshot().sequence != 0:
                    raise AccountingConflict(
                        "active paper account lacks an observation anchor; recover its original "
                        "reference and model identity through explicit migration or create a new account"
                    )
                anchor = {
                    "schema_version": 1,
                    "model_bundle_id": model_bundle_id,
                    "initial_equity": str(self.initial_equity),
                    "initial_price": str(price),
                }
                manifest["observation_anchor"] = anchor
                atomic_write_json(path, manifest)
            if not isinstance(anchor, dict) or set(anchor) != {
                "schema_version",
                "model_bundle_id",
                "initial_equity",
                "initial_price",
            }:
                raise AccountingConflict("invalid paper observation anchor")
            try:
                equity_ref = Decimal(anchor["initial_equity"])
                price_ref = Decimal(anchor["initial_price"])
            except (TypeError, ValueError, ArithmeticError) as exc:
                raise AccountingConflict(
                    "invalid paper observation anchor values"
                ) from exc
            if (
                anchor["schema_version"] != 1
                or anchor["model_bundle_id"] != model_bundle_id
                or equity_ref != self.initial_equity
                or not price_ref.is_finite()
                or price_ref <= ZERO
            ):
                raise AccountingConflict(
                    "paper observation/model contract changed; migrate or create a new account"
                )
            return dict(anchor)

    def snapshot(self, mid: float, observed_at: float):
        price = Decimal(str(mid))
        if (
            not price.is_finite()
            or price <= ZERO
            or not math.isfinite(observed_at)
            or observed_at < 0
        ):
            raise ValueError("paper market observation must be finite and positive")
        when = datetime.fromtimestamp(observed_at, timezone.utc)
        observation_id = (
            "observation-" + hashlib.sha256(str(observed_at).encode()).hexdigest()[:24]
        )
        with self._lock:
            before = self.ledger.snapshot()
            if before.last_event_at and when < before.last_event_at:
                raise ValueError("paper account clock moved backwards")
            prior = self.ledger.event(observation_id)
            if prior is not None:
                if prior.mark_price != price:
                    raise AccountingConflict(
                        "observation timestamp changed its market price"
                    )
                self._last_mid = price
                return state_from_account(
                    before, symbol=self.symbol, quote=self.quote_asset, mid=price
                )
            state = state_from_account(
                before, symbol=self.symbol, quote=self.quote_asset, mid=price
            )
            events = []
            if before.last_event_at:
                elapsed = Decimal(str((when - before.last_event_at).total_seconds()))
                borrowed = state.borrowed_quote + state.borrowed_base * price
                rate = (Decimal(1) + self.daily_rate) ** (
                    elapsed / Decimal(86400)
                ) - Decimal(1)
                cost = borrowed * rate
                if cost > ZERO:
                    events.append(
                        AccountingEvent(
                            event_id=observation_id + ":funding",
                            account_id=self.account_id,
                            event_type=AccountingEventType.FUNDING,
                            occurred_at=when,
                            instrument_id=self.symbol,
                            currency=self.quote_asset,
                            amount=-cost,
                            metadata={"model": "elapsed-compound-daily-assumption"},
                        )
                    )
            if self.symbol in before.positions:
                events.append(
                    AccountingEvent(
                        event_id=observation_id + ":mark",
                        account_id=self.account_id,
                        event_type=AccountingEventType.MARK,
                        occurred_at=when,
                        instrument_id=self.symbol,
                        mark_price=price,
                    )
                )
            events.append(
                AccountingEvent(
                    event_id=observation_id,
                    account_id=self.account_id,
                    event_type=AccountingEventType.OBSERVATION,
                    occurred_at=when,
                    instrument_id=self.symbol,
                    mark_price=price,
                    metadata={"mode": "paper"},
                )
            )
            self.ledger.apply_events(
                events, actor="paper-observation", expected_revision=before.revision
            )
            self._last_mid = price
            return state_from_account(
                self.ledger.snapshot(),
                symbol=self.symbol,
                quote=self.quote_asset,
                mid=price,
            )

    def get_live_equity(self) -> float:
        mid = self._last_mid
        if mid is None:
            mid = Decimal(str(self.api.get_ticker(self.symbol).mid_price))
        return float(
            state_from_account(
                self.ledger.snapshot(),
                symbol=self.symbol,
                quote=self.quote_asset,
                mid=mid,
            ).equity
        )

    def _get_net_position(self) -> Decimal:
        position = self.ledger.snapshot().positions.get(self.symbol)
        return position.quantity if position else ZERO

    async def reconcile(
        self,
        target_position: float,
        current_price: float,
        *,
        decision_id: str,
        observed_at: float,
        spread_bps: float,
        volatility_hint: float = 0.0,
    ) -> dict:
        if not decision_id or len(decision_id) > 160:
            raise ValueError(
                "paper reconcile needs a bounded durable decision identity"
            )
        self.snapshot(current_price, observed_at)
        target = Decimal(str(target_position))
        price = Decimal(str(current_price))
        if not target.is_finite():
            raise ValueError("paper target must be finite")
        when = datetime.fromtimestamp(observed_at, timezone.utc)
        with self._lock:
            prior = self.ledger.event(decision_id + ":intent")
            inputs = {
                "target": str(target),
                "mid": str(price),
                "spread_bps": str(spread_bps),
                "volatility_hint": str(volatility_hint),
            }
            if prior is not None:
                if any(prior.metadata.get(k) != v for k, v in inputs.items()):
                    raise AccountingConflict(
                        "paper decision identity changed its intent"
                    )
                return json.loads(str(prior.metadata["receipt"]))
            before = self.ledger.snapshot()
            state = state_from_account(
                before, symbol=self.symbol, quote=self.quote_asset, mid=price
            )
            rebalance = admit_rebalance(
                current_units=state.position,
                target_units=target,
                equity=state.equity,
                mid=price,
                deadband_fraction=Decimal(
                    str(self.cfg.env.costs.dead_zone_equity_frac)
                ),
            )
            result = self.engine.apply_trade(
                state,
                rebalance.target_units,
                MarketSnapshot(
                    current_price,
                    spread_bps,
                    volatility_hint,
                    self.cfg.env.liquidity.cap_for(self.symbol),
                    self.cfg.env.liquidity.cap_for(self.symbol),
                ),
            )
            receipt = {
                "status": "simulated",
                "execution_authority": "paper",
                "decision_id": decision_id,
                "position": str(result.state.position),
                "equity": str(result.state.equity),
                "delta_units": str(result.delta_units),
                "price": str(result.executed_price),
                "fee_cost": str(result.fee_cost),
                "slippage_cost": str(result.slippage_cost),
                "crossing_cost": str(result.crossing_cost),
                "liquidity_capped": result.liquidity_capped,
                "risk_reduction_only": result.risk_reduction_only,
                "rebalance_reason": rebalance.reason,
            }
            events = execution_accounting_events(
                account_id=self.account_id,
                decision_id=decision_id,
                symbol=self.symbol,
                quote=self.quote_asset,
                occurred_at=when,
                mid=price,
                result=result,
            )
            events.append(
                AccountingEvent(
                    event_id=decision_id + ":intent",
                    account_id=self.account_id,
                    event_type=AccountingEventType.OBSERVATION,
                    occurred_at=when,
                    instrument_id=self.symbol,
                    mark_price=price,
                    metadata={**inputs, "receipt": json.dumps(receipt, sort_keys=True)},
                )
            )
            self.ledger.apply_events(
                events, actor="paper-execution", expected_revision=before.revision
            )
            after = state_from_account(
                self.ledger.snapshot(),
                symbol=self.symbol,
                quote=self.quote_asset,
                mid=price,
            )
            if (
                after.equity != result.state.equity
                or after.position != result.state.position
            ):
                raise AccountingConflict("paper execution/accounting parity failed")
            return receipt

    async def flatten(self) -> bool:
        ticker = await asyncio.to_thread(self.api.get_ticker, self.symbol)
        now = time.time()
        spread = self.cfg.features.assumed_spread_bps
        if spread is None:
            return False
        await self.reconcile(
            0.0,
            float(ticker.mid_price),
            decision_id="paper-flatten-" + str(time.time_ns()),
            observed_at=now,
            spread_bps=spread,
        )
        return self._get_net_position() == ZERO
