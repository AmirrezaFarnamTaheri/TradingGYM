import asyncio
import logging
import math
import time
import uuid
import hashlib
import numpy as np
from decimal import Decimal
from typing import Optional, cast
import requests
from stable_baselines3.common.base_class import BaseAlgorithm
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from trading_gym.execution.live_execution_adapter import LiveExecutionAdapter
from trading_gym.adapters.enhanced_aban_client import EnhancedAbanClient
from trading_gym.common.config import AppConfig
from trading_gym.execution.rebalance_policy import admit_rebalance
from trading_gym.common.persistence import resolve_data_home
from trading_gym.intelligence.decision_journal import DecisionJournal, canonical_digest
from trading_gym.intelligence.types import (
    ActionKind,
    BackendUnavailable,
    DecisionSignal,
    InvalidDecisionSignal,
)
from apps.common.ipc import MetricsWriter
from apps.common.live_features import build_live_feature_snapshot
from apps.common.runtime_files import (
    METRICS_FILE,
    handle_panic_request,
    panic_requested,
    write_heartbeat,
)

logger = logging.getLogger(__name__)


class UnrecognizedExecutionStatus(RuntimeError):
    """The execution adapter returned a state with unknown financial meaning."""


class PanicFlattenUnverified(RuntimeError):
    """Emergency flatten needs explicit reconciliation before further orders."""


def _record_blocked_failure(
    journal, *, decision_id, failure, observation=None, context_sha256=None, **context
):
    """Retain rejection evidence without replacing an earlier admission.

    If the evidence store itself fails, log that failure and preserve the
    original exception. No raw error text, provider response or observation is
    stored. The caller must still halt for a fatal rejection.
    """
    if journal is None or decision_id is None:
        return
    try:
        try:
            prior = journal.decision(decision_id)
        except KeyError:
            context["context_sha256"] = context_sha256 or canonical_digest(
                np.asarray(observation).tolist()
            )
            journal.record_decision(
                decision_id=decision_id,
                signal=None,
                effective_target=None,
                failures=(type(failure).__name__,),
                status="blocked",
                **context,
            )
        else:
            if prior["status"] == "admitted":
                journal.append_event(
                    decision_id=decision_id,
                    event_id=decision_id + ":blocked:" + type(failure).__name__,
                    kind="blocked",
                    occurred_at=context["observed_at"],
                    payload={
                        "error_type": type(failure).__name__,
                        "stage": "after_admission",
                        "execution_verified": False,
                    },
                )
    except Exception as recording_error:
        logger.error(
            "Blocked-decision evidence unavailable error_type=%s",
            type(recording_error).__name__,
        )


async def trade_loop(
    env: MarginTradingEnv,
    model: BaseAlgorithm,
    client: EnhancedAbanClient,
    cfg: AppConfig,
    live_adapter: Optional[LiveExecutionAdapter] = None,
):
    """Main trading loop with optional live execution and metrics reporting."""
    obs, _ = env.reset()
    logger.info("Starting trading loop...")
    # Dashboard metrics are observational, not venue authority. One broken
    # metrics process must not consume the trading failure budget.
    metrics_writer: MetricsWriter | None = None
    metrics_degraded = False

    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 5
    last_base_candle_timestamp: int | None = None
    run_id = uuid.uuid4().hex
    journal = (
        DecisionJournal(resolve_data_home() / "intelligence" / "decisions.sqlite3")
        if live_adapter
        else None
    )
    is_paper = bool(getattr(live_adapter, "owns_simulated_account", False))
    account_id = getattr(live_adapter, "account_id", None) or "session-" + str(
        getattr(live_adapter, "_proposal_id", None) or run_id
    )
    configured_backend = str(
        getattr(getattr(cfg.intelligence, "mode", None), "value", "rl")
    )
    bundle_id = str(getattr(env.unwrapped, "model_bundle_id", "no-model"))
    decision_id = None
    observed_at = 0.0

    while True:
        decision_id = None
        try:
            # --- 0. Heartbeat & Panic Check ---
            write_heartbeat()

            if panic_requested():
                logger.critical("PANIC SIGNAL RECEIVED")
                if not await handle_panic_request(live_adapter):
                    raise PanicFlattenUnverified(
                        "panic flatten unverified; signal retained for reconciliation"
                    )
                logger.critical("SYSTEM HALTED BY USER")
                break

            # Access the underlying environment safely for MyPy
            unwrapped_env = cast(MarginTradingEnv, env.unwrapped)

            if live_adapter:
                tickers = await asyncio.to_thread(client.get_ticker, cfg.env.symbol)
                current_price_float = float(tickers.mid_price)
                observed_at = time.time()
                if is_paper:
                    account_state = await asyncio.to_thread(
                        live_adapter.snapshot, current_price_float, observed_at
                    )
                    real_equity, real_pos_dec = (
                        float(account_state.equity),
                        account_state.position,
                    )
                else:
                    real_equity = float(
                        await asyncio.to_thread(live_adapter.get_live_equity)
                    )
                    real_pos_dec = Decimal(
                        str(await asyncio.to_thread(live_adapter._get_net_position))
                    )
                    account_state = None
                feature_snapshot = await asyncio.to_thread(
                    build_live_feature_snapshot, client, cfg
                )
                if not math.isfinite(real_equity) or not real_pos_dec.is_finite():
                    raise ValueError("account equity and position must be finite")
                if not math.isfinite(current_price_float) or current_price_float <= 0:
                    raise ValueError("live price must be finite and positive")
                risk_halted = unwrapped_env.observe_account_state(
                    equity=Decimal(str(real_equity)),
                    position=real_pos_dec,
                    mid=current_price_float,
                    observed_at=observed_at,
                    average_entry_price=(
                        account_state.average_entry_price
                        if account_state
                        else Decimal(0)
                    ),
                    borrowed_base=(
                        account_state.borrowed_base if account_state else Decimal(0)
                    ),
                    borrowed_quote=(
                        account_state.borrowed_quote if account_state else Decimal(0)
                    ),
                )
                if risk_halted:
                    if not await live_adapter.flatten():
                        raise PanicFlattenUnverified(
                            "account risk halt; flatten unverified"
                        )
                    raise UnrecognizedExecutionStatus(
                        "account risk halt remains latched after flatten"
                    )

                expected_columns = tuple(unwrapped_env.feature_index)
                if feature_snapshot.feature_columns != expected_columns:
                    raise ValueError(
                        "live feature schema does not match the model environment: "
                        f"{feature_snapshot.feature_columns} != {expected_columns}"
                    )
                if (
                    last_base_candle_timestamp is not None
                    and feature_snapshot.base_candle_timestamp
                    < last_base_candle_timestamp
                ):
                    raise ValueError("live candle timestamp moved backwards")
                is_new_closed_candle = (
                    last_base_candle_timestamp is None
                    or feature_snapshot.base_candle_timestamp
                    > last_base_candle_timestamp
                )
                if is_new_closed_candle:
                    unwrapped_env.update_features(feature_snapshot.feature_row)
                    last_base_candle_timestamp = feature_snapshot.base_candle_timestamp

                # Source metadata describes the last FULLY CLOSED market bar,
                # not the later best bid/ask quote. System-1 uses this to
                # reject stale snapshots and avoid fabricated prompt context.
                unwrapped_env.latest_feature_provenance = {
                    "bar_open_unix": int(feature_snapshot.base_candle_timestamp),
                    "bar_seconds": int(cfg.features.timeframes[0].bar_minutes) * 60,
                    "source_sha256": feature_snapshot.source_sha256,
                }
                unwrapped_env.latest_closed_bar_closes = tuple(
                    getattr(feature_snapshot, "base_closed_closes", ())
                )
                # The feature row is validated and computed from CLOSED bars.
                # Preserve a small named subset, without pretending normalized
                # indicators are absolute prices or executable broker balances.
                priority_terms = (
                    "rsi",
                    "atr",
                    "macd",
                    "vol",
                    "spread",
                    "momentum",
                    "trend",
                    "ema",
                    "sma",
                    "return",
                )
                labelled = [
                    (name, float(value))
                    for name, value in zip(
                        feature_snapshot.feature_columns,
                        feature_snapshot.feature_row,
                    )
                    if name and len(name) <= 64
                ]
                labelled.sort(
                    key=lambda pair: (
                        not any(term in pair[0].lower() for term in priority_terms),
                        pair[0],
                    )
                )
                unwrapped_env.latest_feature_summary = dict(labelled[:12])

                # 3. Update the latest tradable mid. Feature generation is based
                # only on fully closed candles; the current ticker is state, not a
                # replacement for the feature vector.
                unwrapped_env.update_latest_candle(current_price_float)

                # Re-fetch observation after state update
                obs = unwrapped_env._obs()

                current_mid = current_price_float
            else:
                # Sim mode price
                current_mid = (
                    unwrapped_env.price[-1]
                    if unwrapped_env.live_mode
                    else unwrapped_env.price[unwrapped_env.t]
                )

                is_new_closed_candle = True

            decision_id = None
            if live_adapter and is_new_closed_candle:
                key = f"{account_id}:{feature_snapshot.base_candle_timestamp}:{bundle_id}:{configured_backend}"
                decision_id = "decision-" + hashlib.sha256(key.encode()).hexdigest()
                # An earlier process may have stopped after admission or submission.
                # The journal requires reconciliation, never automatic re-submission.
                try:
                    journal.decision(decision_id)
                except KeyError:
                    pass
                else:
                    is_new_closed_candle = False
            if not is_new_closed_candle:
                done = truncated = False
                info = unwrapped_env.get_info() | {
                    "skipped": True,
                    "reason": "awaiting_new_closed_candle",
                    "base_candle_timestamp": feature_snapshot.base_candle_timestamp,
                    "feature_source_sha256": feature_snapshot.source_sha256,
                }
            else:
                action, _ = await asyncio.to_thread(
                    model.predict, obs, deterministic=True
                )
                logger.debug(f"Predicted action: {action}")

                # Hosted System-1 or System-2 inference can take seconds.
                # A panic may have arrived AFTER the top-of-loop check.
                # Recheck before constructing or sending a live target.
                if live_adapter and panic_requested():
                    if not await handle_panic_request(live_adapter):
                        raise PanicFlattenUnverified(
                            "panic during inference; flatten unverified"
                        )
                    logger.critical("SYSTEM HALTED BY USER")
                    break

            if live_adapter and is_new_closed_candle:
                # In live mode, execute the trade via adapter instead of stepping
                action_array = np.asarray(action)
                if action_array.size != 1:
                    raise ValueError(
                        "single-instrument policy must return exactly one action"
                    )
                action_value = float(action_array.item())
                if not math.isfinite(action_value):
                    raise ValueError("model action must be finite")
                target_leverage = Decimal(str(action_value))
                target_position_notional = target_leverage * unwrapped_env.equity
                target_position_units = float(target_position_notional) / max(
                    current_mid, 1e-12
                )

                # ROBUSTNESS: Clip target position to environment limits (leverage/caps)
                # This ensures the live execution respects the same rules as the simulation.
                clipped_target_units = unwrapped_env.clip_target_position(
                    target_position_units
                )

                if abs(clipped_target_units - target_position_units) > 1e-9:
                    logger.warning(
                        f"Target position clipped by environment rules: "
                        f"{target_position_units:.6f} -> {clipped_target_units:.6f}"
                    )

                rebalance = admit_rebalance(
                    current_units=real_pos_dec,
                    target_units=Decimal(str(clipped_target_units)),
                    equity=unwrapped_env.equity,
                    mid=Decimal(str(current_mid)),
                    deadband_fraction=Decimal(str(cfg.env.costs.dead_zone_equity_frac)),
                )
                clipped_target_units = float(rebalance.target_units)
                signal = getattr(model, "last_signal", None)
                if signal is None:
                    signal = DecisionSignal(
                        backend="rl",
                        action_kind=(
                            ActionKind.LONG
                            if action_value > 0
                            else (
                                ActionKind.SHORT
                                if action_value < 0
                                else ActionKind.HOLD
                            )
                        ),
                        target_leverage=action_value,
                        confidence=0.0,
                        rationale="hash-admitted RL action; confidence not estimated",
                    )
                journal.record_decision(
                    decision_id=decision_id,
                    run_id=run_id,
                    account_id=account_id,
                    mode="paper" if is_paper else "live",
                    configured_backend=configured_backend,
                    signal=signal,
                    effective_target=clipped_target_units,
                    observed_at=observed_at,
                    context_sha256=canonical_digest(np.asarray(obs).tolist()),
                    model_bundle_id=bundle_id,
                    failures=tuple(getattr(model, "last_failure_types", ())),
                )
                journal.append_event(
                    decision_id=decision_id,
                    event_id=decision_id + ":intent",
                    kind="intent",
                    occurred_at=observed_at,
                    payload={
                        "target_units": clipped_target_units,
                        "reference_mid": current_mid,
                    },
                )
                if is_paper:
                    raw_features = unwrapped_env._get_current_raw_features()
                    execution_result = await live_adapter.reconcile(
                        clipped_target_units,
                        current_mid,
                        decision_id=decision_id,
                        observed_at=observed_at,
                        spread_bps=float(
                            raw_features[unwrapped_env.feature_index["spread_ratio"]]
                        )
                        * 10000,
                        volatility_hint=float(
                            raw_features[unwrapped_env.feature_index["volatility"]]
                        ),
                    )
                else:
                    execution_result = await live_adapter.reconcile(
                        clipped_target_units, current_mid
                    )
                if not isinstance(execution_result, dict):
                    raise RuntimeError("execution adapter returned an invalid result")
                status = execution_result.get("status")
                if status == "failed":
                    # Even a locally classified failure can follow an order
                    # submission whose venue outcome is not independently
                    # settled. No further funded decisions until reconciliation.
                    raise UnrecognizedExecutionStatus(
                        "live execution failed; broker reconciliation required"
                    )
                if status == "skipped":
                    if execution_result.get("reason") not in {
                        "dust",
                        "no_executable_leg",
                    }:
                        # A denial (e.g. short_not_allowed, locked or unknown)
                        # is not a harmless idle candle or retryable outage.
                        raise UnrecognizedExecutionStatus(
                            "live reconciliation denied; operator review required"
                        )
                elif status not in {"executed", "simulated"}:
                    # No code should interpret an unknown venue result as
                    # success or continue submitting on a later candle.
                    raise UnrecognizedExecutionStatus(
                        "unrecognized live execution outcome"
                    )

                if is_paper:
                    settled = await asyncio.to_thread(
                        live_adapter.snapshot, current_mid, observed_at
                    )
                    post_trade_halt = unwrapped_env.observe_account_state(
                        equity=settled.equity,
                        position=settled.position,
                        mid=current_mid,
                        observed_at=observed_at,
                        average_entry_price=settled.average_entry_price,
                        borrowed_base=settled.borrowed_base,
                        borrowed_quote=settled.borrowed_quote,
                    )
                    journal.append_event(
                        decision_id=decision_id,
                        event_id=decision_id + ":settlement",
                        kind="settlement",
                        occurred_at=observed_at,
                        payload={
                            "authority": "paper",
                            "account_id": account_id,
                            "ledger_chain_head": live_adapter.ledger.snapshot().chain_head,
                            "equity": str(settled.equity),
                            "position": str(settled.position),
                            "receipt": execution_result,
                        },
                    )
                    if post_trade_halt:
                        if not await live_adapter.flatten():
                            raise PanicFlattenUnverified(
                                "post-trade risk halt; flatten unverified"
                            )
                        raise UnrecognizedExecutionStatus(
                            "post-trade account halt remains latched"
                        )
                else:
                    # An acknowledgement is recorded as an order outcome. Only a
                    # later independent broker reconciliation can label a fill.
                    journal.append_event(
                        decision_id=decision_id,
                        event_id=decision_id + ":order",
                        kind="order",
                        occurred_at=time.time(),
                        payload={"status": status, "settlement_verified": False},
                    )
                obs = unwrapped_env._obs()
                done = truncated = False
                info = env.get_info() | {
                    "base_candle_timestamp": feature_snapshot.base_candle_timestamp,
                    "feature_source_sha256": feature_snapshot.source_sha256,
                    "execution_status": status,
                }
            elif not live_adapter:
                # In simulation mode, step the environment normally
                obs, _, done, truncated, info = env.step(action)
                # StepInfo exposes a symbol-to-position map. Dashboard metrics
                # need the same authoritative account fields as live polling;
                # absent scalar fields must not fabricate a flat, zero-P&L view.
                info = info | unwrapped_env.get_info()

            logger.debug(f"Step completed. Done: {done}, Truncated: {truncated}")
            logger.debug(f"Info: {info}")

            # An idle poll of the same closed candle is not proof that the
            # previous failed execution recovered. Keep its error budget.
            if not live_adapter or is_new_closed_candle:
                consecutive_errors = 0

            # --- IPC REPLACEMENT: Send metrics to the API server ---
            metrics = {
                "balance": info.get("equity", 0),
                "pnl": info.get("pnl", 0),
                "total_positions": 1 if info.get("position", 0) != 0 else 0,
                "positions": [
                    {
                        "symbol": cfg.env.symbol,
                        "size": info.get("position", 0),
                        "entry_price": info.get("entry_price", 0),
                    }
                ],
                "timestamp": time.time(),
            }
            try:
                if metrics_writer is None:
                    metrics_writer = MetricsWriter(METRICS_FILE)
                metrics_writer.write(metrics)
            except Exception as exc:
                if not metrics_degraded:
                    logger.warning(
                        "Optional metrics IPC unavailable error_type=%s",
                        type(exc).__name__,
                    )
                metrics_degraded = True
                metrics_writer = None
            else:
                if metrics_degraded:
                    logger.info("Optional metrics IPC recovered")
                metrics_degraded = False
            # --------------------------------------------------------

            if (done or truncated) and not live_adapter:
                logger.warning("Episode finished. Resetting environment.")
                obs, _ = env.reset()

        except BackendUnavailable as exc:
            if decision_id is not None:
                _record_blocked_failure(
                    journal,
                    decision_id=decision_id,
                    failure=exc,
                    run_id=run_id,
                    account_id=account_id,
                    mode="paper" if is_paper else "live",
                    configured_backend=configured_backend,
                    observed_at=observed_at,
                    observation=obs,
                    model_bundle_id=bundle_id,
                )
            logger.warning("Decision blocked error_type=%s", type(exc).__name__)
            consecutive_errors += 1
        except (
            UnrecognizedExecutionStatus,
            PanicFlattenUnverified,
            InvalidDecisionSignal,
            PermissionError,
        ) as exc:
            if decision_id is not None:
                _record_blocked_failure(
                    journal,
                    decision_id=decision_id,
                    failure=exc,
                    run_id=run_id,
                    account_id=account_id,
                    mode="paper" if is_paper else "live",
                    configured_backend=configured_backend,
                    observed_at=observed_at,
                    observation=obs,
                    model_bundle_id=bundle_id,
                )
            logger.critical("Unverified live outcome; halting for reconciliation")
            raise
        except requests.RequestException as exc:
            logger.error("API request failed error_type=%s", type(exc).__name__)
            consecutive_errors += 1
        except ValueError as exc:
            logger.error("Data validation failed error_type=%s", type(exc).__name__)
            consecutive_errors += 1
        except Exception as exc:
            logger.critical("Trade loop failed error_type=%s", type(exc).__name__)
            consecutive_errors += 1

        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            logger.critical(
                f"Exceeded max consecutive errors ({MAX_CONSECUTIVE_ERRORS}). "
                "Stopping trade loop to prevent further issues."
            )
            break

        # Bound provider/model polling under failure, then automatically
        # restore the configured fast cadence after a healthy cycle.
        healthy_interval = float(cfg.paper_trader.loop_interval_seconds)
        if not math.isfinite(healthy_interval) or healthy_interval <= 0:
            raise ValueError("trading loop interval must be finite and positive")
        backoff = 2 ** min(consecutive_errors, 4) if consecutive_errors else 1
        await asyncio.sleep(min(60.0, healthy_interval * backoff))
