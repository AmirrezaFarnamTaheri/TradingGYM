import asyncio
import logging
import math
import time
from decimal import Decimal
from typing import Optional, cast
import requests
from stable_baselines3.common.base_class import BaseAlgorithm
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from trading_gym.execution.live_execution_adapter import LiveExecutionAdapter
from trading_gym.adapters.enhanced_aban_client import EnhancedAbanClient
from trading_gym.common.config import AppConfig
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

    while True:
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
                # Read market state sequentially; no redundant async wrappers.
                real_equity = float(
                    await asyncio.to_thread(live_adapter.get_live_equity)
                )
                real_pos_dec = Decimal(
                    str(await asyncio.to_thread(live_adapter._get_net_position))
                )
                tickers = await asyncio.to_thread(client.get_ticker, cfg.env.symbol)
                current_price_float = float(tickers.mid_price)
                feature_snapshot = await asyncio.to_thread(
                    build_live_feature_snapshot, client, cfg
                )
                if not math.isfinite(real_equity) or real_equity <= 0:
                    raise ValueError("live equity must be finite and positive")
                if not real_pos_dec.is_finite():
                    raise ValueError("live position must be finite")
                if not math.isfinite(current_price_float) or current_price_float <= 0:
                    raise ValueError("live price must be finite and positive")

                # 2. Force-update the environment's internal state to match reality
                unwrapped_env._state.position = real_pos_dec
                unwrapped_env._state.equity = Decimal(str(real_equity))

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

            if not is_new_closed_candle:
                done = truncated = False
                info = unwrapped_env.get_info() | {
                    "skipped": True,
                    "reason": "awaiting_new_closed_candle",
                    "base_candle_timestamp": feature_snapshot.base_candle_timestamp,
                    "feature_source_sha256": feature_snapshot.source_sha256,
                }
            else:
                action, _ = model.predict(obs, deterministic=True)
                logger.debug(f"Predicted action: {action}")

            if live_adapter and is_new_closed_candle:
                # In live mode, execute the trade via adapter instead of stepping
                action_value = float(action.item())
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

                execution_result = await live_adapter.reconcile(
                    clipped_target_units, current_mid
                )
                if not isinstance(execution_result, dict):
                    raise RuntimeError("execution adapter returned an invalid result")
                status = execution_result.get("status")
                if status == "failed":
                    raise RuntimeError(
                        f"live execution failed ({execution_result.get('error_type', 'unknown')})"
                    )
                if status == "skipped":
                    if execution_result.get("reason") not in {"dust", "no_executable_leg"}:
                        raise RuntimeError("live reconciliation skipped without fill")
                elif status not in {"executed", "simulated"}:
                    # No code should interpret an unknown venue result as
                    # success or continue submitting on a later candle.
                    raise UnrecognizedExecutionStatus(
                        "unrecognized live execution outcome"
                    )

                # A venue submission acknowledgement is NOT an observed fill;
                # the broker may still be processing or partially filling it.
                # Never publish the model target as an actual broker position.
                # The next cycle independently observes authoritative balances.
                if status == "simulated":
                    synchronized_position = Decimal(str(clipped_target_units))
                else:
                    synchronized_position = real_pos_dec
                unwrapped_env._state.position = synchronized_position
                # Step the environment with a dummy action to advance time (if env supports it)
                # In live_mode, step(0) might be a no-op but we call it to consistency checks if any
                _, _, done, truncated, _ = env.step(0)
                info = env.get_info() | {
                    "base_candle_timestamp": feature_snapshot.base_candle_timestamp,
                    "feature_source_sha256": feature_snapshot.source_sha256,
                    "execution_status": status,
                }
            elif not live_adapter:
                # In simulation mode, step the environment normally
                obs, _, done, truncated, info = env.step(action)

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

            if done or truncated:
                logger.warning("Episode finished. Resetting environment.")
                obs, _ = env.reset()

        except (UnrecognizedExecutionStatus, PanicFlattenUnverified):
            logger.critical(
                "Unverified live outcome; halting for reconciliation"
            )
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
