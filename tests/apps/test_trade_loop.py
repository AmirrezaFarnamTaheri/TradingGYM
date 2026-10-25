import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
from decimal import Decimal
import numpy as np
import pandas as pd
from stable_baselines3.common.base_class import BaseAlgorithm

from trading_gym.data.live_features import LiveFeatureSnapshot
from trading_gym.execution.trade_loop import trade_loop
from trading_gym.market.adapters.enhanced_aban_client import EnhancedAbanClient
from trading_gym.core.config import load_app_config, PaperTraderConfig
from trading_gym.execution.live_execution_adapter import LiveExecutionAdapter
from trading_gym.execution.paper_execution_adapter import PaperExecutionAdapter
from trading_gym.agent.intelligence.decision_journal import DecisionJournal
from trading_gym.rl.margin_trading_environment import MarginTradingEnv


ROOT = Path(__file__).resolve().parents[2]


class TestTradeLoop(unittest.IsolatedAsyncioTestCase):
    def make_feature_snapshot(self):
        return LiveFeatureSnapshot(
            feature_columns=("spread_ratio", "volatility"),
            feature_row=np.array([0.002, 0.0], dtype=np.float32),
            base_candle_timestamp=1_000,
            base_close=50_000.0,
            base_high=50_100.0,
            base_low=49_900.0,
            source_sha256="a" * 64,
            timeframe_timestamps={"5m": 1_000},
            base_closed_closes=(49_900.0, 50_000.0),
        )

    def use_environment(self, *, live=False):
        features = pd.DataFrame(
            {"spread_ratio": np.full(4, 0.002), "volatility": np.zeros(4)}
        )
        prices = np.full(4, 50_000.0)
        self.account_view = MarginTradingEnv(
            features,
            prices,
            prices,
            prices,
            self.cfg.env,
            self.cfg.features,
            live_mode=live,
            training_stats={
                "center": {name: 0 for name in features},
                "scale": {name: 1 for name in features},
            },
        )
        self.account_view.model_bundle_id = "fixture-model"
        self.env = MagicMock(spec=MarginTradingEnv, wraps=self.account_view)
        self.env.unwrapped = self.account_view

    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_home = Path(directory.name)
        self.cfg = load_app_config(ROOT / "config/app.yaml")
        self.cfg.paper_trader = PaperTraderConfig(
            trade_size_pct_equity=0.1, loop_interval_seconds=1
        )
        self.cfg.env.breaker.state_file = None
        self.cfg.env.slippage.volatility_lambda = 0
        self.cfg.env.slippage.liquidity_lambda = 0
        self.use_environment()

        self.model = MagicMock(spec=BaseAlgorithm)
        self.model.predict.return_value = (np.array([0.5]), None)
        self.model.last_signal = None
        self.model.last_failure_types = ()
        self.client = MagicMock(spec=EnhancedAbanClient)
        self.live_adapter = MagicMock(spec=LiveExecutionAdapter)
        self.live_adapter.owns_simulated_account = False
        self.live_adapter.account_id = "live-fixture-account"
        self.live_adapter.get_live_equity.return_value = 10000.0
        # Acknowledging a target of 0.1 is not evidence that this observed
        # broker position has changed to that target.
        self.live_adapter._get_net_position.return_value = Decimal("0.05")
        self.live_adapter.reconcile = AsyncMock(return_value={"status": "executed"})
        self.client.get_ticker.return_value = SimpleNamespace(
            mid_price=Decimal("50000.0")
        )

        self.feature_snapshot = self.make_feature_snapshot()
        for name, value in (
            ("build_live_feature_snapshot", self.feature_snapshot),
            ("resolve_data_home", self.data_home),
            ("write_heartbeat", None),
            ("panic_requested", False),
        ):
            patcher = patch(f"trading_gym.execution.trade_loop.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def decision_journal(self):
        return DecisionJournal(self.data_home / "intelligence" / "decisions.sqlite3")

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_panic_arriving_during_model_inference_prevents_live_reconciliation(
        self, mock_sleep, mock_writer
    ):
        """No newly inferred order may be sent after a panic is raised."""
        self.use_environment(live=True)
        pending = {"raised": False}

        def infer_then_request_panic(*args, **kwargs):
            pending["raised"] = True
            return np.array([0.5]), None

        self.model.predict.side_effect = infer_then_request_panic
        with (
            patch(
                "trading_gym.execution.trade_loop.panic_requested",
                side_effect=lambda: pending["raised"],
            ),
            patch(
                "trading_gym.execution.trade_loop.handle_panic_request",
                new_callable=AsyncMock,
                return_value=True,
            ) as flatten,
        ):
            await trade_loop(
                self.env,
                self.model,
                self.client,
                self.cfg,
                live_adapter=self.live_adapter,
            )

        flatten.assert_awaited_once_with(self.live_adapter)
        self.live_adapter.reconcile.assert_not_called()
        self.env.step.assert_not_called()
        mock_writer.return_value.write.assert_not_called()

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_trade_loop_simulation_basic(self, mock_sleep, MockMetricsWriter):
        mock_sleep.side_effect = [None, RuntimeError("Stop Loop")]

        with self.assertRaisesRegex(RuntimeError, "Stop Loop"):
            await trade_loop(
                self.env, self.model, self.client, self.cfg, live_adapter=None
            )

        self.env.reset.assert_called()
        self.model.predict.assert_called()
        self.env.step.assert_called()

        MockMetricsWriter.return_value.write.assert_called()
        args = MockMetricsWriter.return_value.write.call_args[0][0]
        self.assertEqual(args["balance"], float(self.account_view.equity))
        self.assertLess(args["balance"], 10000.0)
        self.assertGreater(args["balance"], 0)
        self.assertEqual(args["pnl"], float(self.account_view.equity) - 10000.0)
        self.assertLess(args["pnl"], 0)
        self.assertEqual(args["total_positions"], 1)
        self.assertEqual(args["positions"][0]["symbol"], "BTCUSDT")
        self.assertEqual(
            args["positions"][0]["size"], float(self.account_view.position)
        )
        self.assertEqual(
            args["positions"][0]["entry_price"],
            self.account_view.get_info()["entry_price"],
        )
        self.assertGreater(args["positions"][0]["entry_price"], 0)

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_trade_loop_live_adapter(self, mock_sleep, MockMetricsWriter):
        self.use_environment(live=True)
        mock_sleep.side_effect = [None, RuntimeError("Stop Loop")]

        with self.assertRaisesRegex(RuntimeError, "Stop Loop"):
            await trade_loop(
                self.env,
                self.model,
                self.client,
                self.cfg,
                live_adapter=self.live_adapter,
            )
        self.assertEqual(self.account_view.price[-1], 50000.0)

        self.live_adapter.reconcile.assert_awaited_once()
        call_args = self.live_adapter.reconcile.call_args[0]
        self.assertAlmostEqual(float(call_args[0]), 0.1, places=5)
        self.assertEqual(float(call_args[1]), 50000.0)

        self.env.step.assert_not_called()
        # "executed" is an acknowledgement, not a verified filled position.
        self.assertEqual(self.account_view.position, Decimal("0.05"))
        self.assertEqual(self.account_view.equity, Decimal("10000.0"))
        self.model.predict.assert_called_once()
        journal = self.decision_journal()
        (decision,) = journal.decisions(account_id=self.live_adapter.account_id)
        self.assertEqual(decision["mode"], "live")
        (order,) = journal.events(decision["decision_id"], kind="order")
        self.assertEqual(
            order["payload"], {"status": "executed", "settlement_verified": False}
        )
        self.assertEqual(journal.events(kind="fill"), ())
        self.assertEqual(journal.events(kind="settlement"), ())

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_paper_account_owns_settlement_without_environment_stepping(
        self, mock_sleep, mock_writer
    ):
        self.use_environment(live=True)
        paper = PaperExecutionAdapter(
            self.cfg,
            account_path=self.data_home / "paper.json",
            initial_equity=Decimal("10000"),
            market_client=self.client,
        )
        self.client.get_ticker.side_effect = [
            SimpleNamespace(mid_price=Decimal("50000")),
            SimpleNamespace(mid_price=Decimal("55000")),
        ]
        mock_sleep.side_effect = [None, RuntimeError("Stop Loop")]

        with self.assertRaisesRegex(RuntimeError, "Stop Loop"):
            await trade_loop(
                self.env, self.model, self.client, self.cfg, live_adapter=paper
            )

        self.env.step.assert_not_called()
        self.model.predict.assert_called_once()
        self.assertEqual(self.account_view.position, paper._get_net_position())
        self.assertGreater(self.account_view.position, Decimal(0))
        self.assertEqual(float(self.account_view.equity), paper.get_live_equity())
        self.assertGreater(self.account_view.equity, Decimal("10000"))
        self.assertEqual({call[0] for call in self.client.mock_calls}, {"get_ticker"})
        journal = self.decision_journal()
        (decision,) = journal.decisions(account_id=paper.account_id)
        self.assertEqual(decision["mode"], "paper")
        (settlement,) = journal.events(decision["decision_id"], kind="settlement")
        self.assertEqual(settlement["payload"]["authority"], "paper")
        self.assertEqual(settlement["payload"]["account_id"], paper.account_id)
        self.assertEqual(journal.events(kind="order"), ())
        metrics = mock_writer.return_value.write.call_args.args[0]
        self.assertEqual(metrics["balance"], float(self.account_view.equity))
        self.assertEqual(
            metrics["positions"][0]["size"], float(paper._get_net_position())
        )

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_trade_loop_error_handling(self, mock_sleep, MockMetricsWriter):
        self.model.predict.side_effect = ValueError("Data Error")
        mock_sleep.return_value = None

        await trade_loop(self.env, self.model, self.client, self.cfg, live_adapter=None)

        self.assertEqual(self.model.predict.call_count, 5)

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_unknown_adapter_status_does_not_publish_success(
        self, mock_sleep, mock_writer
    ):
        self.use_environment(live=True)
        self.live_adapter.reconcile.return_value = {"status": "broker_maybe"}
        with self.assertRaisesRegex(RuntimeError, "unrecognized live execution"):
            await trade_loop(
                self.env,
                self.model,
                self.client,
                self.cfg,
                live_adapter=self.live_adapter,
            )
        self.assertEqual(self.live_adapter.reconcile.call_count, 1)
        self.env.step.assert_not_called()
        mock_writer.return_value.write.assert_not_called()

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_rejected_short_entry_is_not_reported_as_success(
        self, mock_sleep, mock_writer
    ):
        self.use_environment(live=True)
        self.live_adapter.reconcile.return_value = {
            "status": "skipped",
            "reason": "short_not_allowed",
        }
        # A denied broker operation must halt; an unrelated next candle
        # cannot magically turn an authorization denial into permission.
        mock_sleep.side_effect = AssertionError("denied live operation was retried")
        with self.assertRaisesRegex(RuntimeError, "live reconciliation denied"):
            await trade_loop(
                self.env,
                self.model,
                self.client,
                self.cfg,
                live_adapter=self.live_adapter,
            )
        self.assertEqual(self.live_adapter.reconcile.call_count, 1)
        mock_sleep.assert_not_awaited()
        self.env.step.assert_not_called()
        mock_writer.return_value.write.assert_not_called()

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_optional_metrics_failure_does_not_stop_healthy_trading(
        self, mock_sleep, mock_writer
    ):
        output = mock_writer.return_value
        output.write.side_effect = [OSError("unavailable"), None]
        mock_sleep.side_effect = [None, RuntimeError("Stop Loop")]
        with self.assertRaisesRegex(RuntimeError, "Stop Loop"):
            await trade_loop(self.env, self.model, self.client, self.cfg)
        self.assertEqual(self.model.predict.call_count, 2)
        self.assertEqual(output.write.call_count, 2)

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_optional_metrics_writer_initialization_recovers(
        self, mock_sleep, mock_writer
    ):
        writer = MagicMock()
        mock_writer.side_effect = [OSError("metrics dir unavailable"), writer]
        mock_sleep.side_effect = [None, RuntimeError("Stop Loop")]
        with self.assertRaisesRegex(RuntimeError, "Stop Loop"):
            await trade_loop(self.env, self.model, self.client, self.cfg)
        self.assertEqual(self.model.predict.call_count, 2)
        self.assertEqual(mock_writer.call_count, 2)
        writer.write.assert_called_once()

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_provider_failure_backoff_restores_healthy_fast_cadence(
        self, mock_sleep, mock_writer
    ):
        self.model.predict.side_effect = [
            ValueError("invalid"),
            (np.array([0.0]), None),
        ]
        mock_sleep.side_effect = [None, RuntimeError("Stop Loop")]
        with self.assertRaisesRegex(RuntimeError, "Stop Loop"):
            await trade_loop(self.env, self.model, self.client, self.cfg)
        intervals = [call.args[0] for call in mock_sleep.await_args_list]
        self.assertEqual(intervals, [2.0, 1.0])

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_subminimum_execution_skip_preserves_real_position_and_telemetry(
        self, mock_sleep, mock_writer
    ):
        self.use_environment(live=True)
        self.live_adapter.reconcile.return_value = {
            "status": "skipped",
            "reason": "no_executable_leg",
        }
        mock_sleep.side_effect = [RuntimeError("Stop Loop")]
        with self.assertRaisesRegex(RuntimeError, "Stop Loop"):
            await trade_loop(
                self.env,
                self.model,
                self.client,
                self.cfg,
                live_adapter=self.live_adapter,
            )
        self.live_adapter.reconcile.assert_awaited_once()
        self.env.step.assert_not_called()
        mock_writer.return_value.write.assert_called_once()
        self.assertEqual(self.account_view.position, Decimal("0.05"))
        metrics = mock_writer.return_value.write.call_args.args[0]
        self.assertEqual(metrics["positions"][0]["size"], 0.05)

    @patch("trading_gym.execution.trade_loop.panic_requested", return_value=True)
    @patch("trading_gym.execution.trade_loop.handle_panic_request", new_callable=AsyncMock)
    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    async def test_failed_panic_flatten_halts_worker_without_repeating(
        self, mock_writer, mock_handle_panic, mock_panic_requested
    ):
        self.use_environment(live=True)
        mock_handle_panic.return_value = False
        with self.assertRaisesRegex(RuntimeError, "panic flatten unverified"):
            await trade_loop(
                self.env,
                self.model,
                self.client,
                self.cfg,
                live_adapter=self.live_adapter,
            )
        mock_handle_panic.assert_awaited_once_with(self.live_adapter)
        self.live_adapter.reconcile.assert_not_called()
        mock_writer.return_value.write.assert_not_called()

    @patch("trading_gym.execution.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_failed_live_reconciliation_halts_without_retrying_next_bar(
        self, mock_sleep, mock_writer
    ):
        """A failed broker reconciliation is not a recoverable model outage."""
        self.use_environment(live=True)
        self.live_adapter.reconcile.return_value = {
            "status": "failed",
            "error_type": "MutationOutcomeUnknown",
        }
        mock_sleep.side_effect = AssertionError("failed live mutation was retried")
        with self.assertRaisesRegex(RuntimeError, "live execution failed"):
            await trade_loop(
                self.env,
                self.model,
                self.client,
                self.cfg,
                live_adapter=self.live_adapter,
            )
        self.live_adapter.reconcile.assert_awaited_once()
        mock_sleep.assert_not_awaited()
        self.env.step.assert_not_called()
        mock_writer.return_value.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
