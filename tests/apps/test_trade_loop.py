import unittest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from decimal import Decimal
import numpy as np
from apps.common.trade_loop import trade_loop
from trading_gym.common.config import AppConfig, EnvConfig, PaperTraderConfig


class TestTradeLoop(unittest.IsolatedAsyncioTestCase):
    def make_feature_snapshot(self):
        snapshot = MagicMock()
        snapshot.feature_columns = ("log_ret", "volatility")
        snapshot.base_candle_timestamp = 1_000
        snapshot.source_sha256 = "abc"
        snapshot.feature_row = np.zeros(2, dtype=np.float32)
        return snapshot

    async def asyncSetUp(self):
        self.env = MagicMock()
        self.model = MagicMock()
        self.client = MagicMock()
        self.live_adapter = MagicMock()

        self.cfg = MagicMock(spec=AppConfig)
        self.cfg.env = MagicMock(spec=EnvConfig)
        self.cfg.env.symbol = "BTCUSDT"
        self.cfg.symbol = "BTCUSDT"

        self.cfg.paper_trader = MagicMock(spec=PaperTraderConfig)
        self.cfg.paper_trader.loop_interval_seconds = 0.01

        self.env.reset.return_value = (MagicMock(), {})
        self.env.step.return_value = (
            MagicMock(),
            0.0,
            False,
            False,
            {"equity": 10000.0, "pnl": 50.0, "position": 0.1, "entry_price": 50000.0},
        )
        self.env.get_info.return_value = {
            "equity": 10000.0,
            "pnl": 50.0,
            "position": 0.1,
            "entry_price": 50000.0,
        }
        self.env.unwrapped = MagicMock()
        self.env.unwrapped._state = MagicMock()
        self.env.unwrapped._state.equity = Decimal("10000.0")
        self.env.unwrapped._state.position = Decimal("0.0")
        self.env.unwrapped.live_mode = True
        self.env.unwrapped.price = [50000.0]
        # Feature schema contract checked by trade_loop before execution
        self.env.unwrapped.feature_index = {"log_ret": 0, "volatility": 1}
        type(self.env.unwrapped).equity = PropertyMock(
            side_effect=lambda: self.env.unwrapped._state.equity
        )
        # MOCK THE NEW CLIP METHOD
        self.env.unwrapped.clip_target_position.side_effect = lambda x: x

        self.model.predict.return_value = (MagicMock(item=lambda: 0.5), None)

        self.live_adapter.get_live_equity.return_value = 10000.0
        self.live_adapter._get_net_position.return_value = Decimal("0.1")

        # Make reconcile an AsyncMock because it is awaited in trade_loop
        self.live_adapter.reconcile = AsyncMock()
        self.live_adapter.reconcile.return_value = {"status": "executed"}

        self.client.get_ticker.return_value = MagicMock(mid_price=Decimal("50000.0"))

        self.feature_snapshot = self.make_feature_snapshot()
        patcher = patch(
            "apps.common.trade_loop.build_live_feature_snapshot",
            return_value=self.feature_snapshot,
        )
        self._snapshot_patcher = patcher.start()
        self.addCleanup(patcher.stop)

    @patch("apps.common.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_trade_loop_simulation_basic(self, mock_sleep, MockMetricsWriter):
        mock_sleep.side_effect = [None, RuntimeError("Stop Loop")]

        try:
            await trade_loop(
                self.env, self.model, self.client, self.cfg, live_adapter=None
            )
        except RuntimeError:
            pass

        self.env.reset.assert_called()
        self.model.predict.assert_called()
        self.env.step.assert_called()

        MockMetricsWriter.return_value.write.assert_called()
        args = MockMetricsWriter.return_value.write.call_args[0][0]
        self.assertEqual(args["balance"], 10000.0)
        self.assertEqual(args["positions"][0]["symbol"], "BTCUSDT")

    @patch("apps.common.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_trade_loop_live_adapter(self, mock_sleep, MockMetricsWriter):
        mock_sleep.side_effect = [None, RuntimeError("Stop Loop")]

        try:
            await trade_loop(
                self.env,
                self.model,
                self.client,
                self.cfg,
                live_adapter=self.live_adapter,
            )
        except RuntimeError:
            pass

        self.env.unwrapped.update_latest_candle.assert_called_with(50000.0)

        self.live_adapter.reconcile.assert_called()
        call_args = self.live_adapter.reconcile.call_args[0]
        self.assertAlmostEqual(float(call_args[0]), 0.1, places=5)
        self.assertEqual(float(call_args[1]), 50000.0)

        self.env.step.assert_called_with(0)

    @patch("apps.common.trade_loop.MetricsWriter")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_trade_loop_error_handling(self, mock_sleep, MockMetricsWriter):
        self.model.predict.side_effect = ValueError("Data Error")
        mock_sleep.return_value = None

        await trade_loop(self.env, self.model, self.client, self.cfg, live_adapter=None)

        self.assertEqual(self.model.predict.call_count, 5)


if __name__ == "__main__":
    unittest.main()
