from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from axiomforge.live_trading import LiveConsent
from trading_gym.execution.live_execution_adapter import LiveExecutionAdapter


def _config():
    cfg = MagicMock()
    cfg.env.symbol = "BTCUSDT"
    cfg.env.quote_ccy = "USDT"
    cfg.env.decimals.qty_for.return_value = 6
    cfg.env.decimals.price_for.return_value = 2
    cfg.env.decimals.min_qty_for.return_value = 0.001
    return cfg


def _consent():
    return LiveConsent(
        actor="operator",
        scope="live.execute",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        nonce="fifteenth-pass",
    )


@pytest.fixture(autouse=True)
def isolated_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))


@pytest.mark.asyncio
async def test_live_adapter_fails_closed_before_any_sdk_mutation_without_consent():
    sdk = MagicMock()
    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=sdk,
    ):
        adapter = LiveExecutionAdapter(_config(), "token")

    with pytest.raises(PermissionError, match="governed_live_consent_required"):
        await adapter._mutate("spot.market", side="buy", quantity=Decimal("1"))

    sdk.place_market_order.assert_not_called()
    sdk.create_short_sell.assert_not_called()
    sdk.close_short_position.assert_not_called()
    sdk.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_live_adapter_delegates_mutation_to_single_production_coordinator():
    sdk = MagicMock()
    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=sdk,
    ):
        adapter = LiveExecutionAdapter(
            _config(),
            "token",
            live_consent=_consent(),
            proposal_id="proposal-1",
        )

    coordinator = MagicMock()
    authorization = object()
    coordinator.bind_authorization.return_value = authorization
    coordinator.execute.return_value = {"result": {"id": "coordinated"}}
    adapter._coordinator = coordinator

    result = await adapter._mutate(
        "spot.market",
        side="buy",
        quantity=Decimal("0.25"),
    )

    assert result == {"id": "coordinated"}
    coordinator.bind_authorization.assert_called_once()
    coordinator.execute.assert_called_once()
    sdk.place_market_order.assert_not_called()
    sdk.create_short_sell.assert_not_called()
    sdk.close_short_position.assert_not_called()
    sdk.cancel_order.assert_not_called()
