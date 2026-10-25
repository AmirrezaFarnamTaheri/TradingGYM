import json
import time
from types import SimpleNamespace

import httpx
import pytest

from trading_gym.agent.daemon import AgentSettings
from trading_gym.agent.decision_history import DecisionHistory
from trading_gym.execution.live.cockpit_orders import FundedExecution
from trading_gym.market.adapters.cockpit_abantether import AbantetherConnection


def connection(tmp_path):
    history = DecisionHistory(tmp_path / "history.sqlite3")
    vault = SimpleNamespace(key=lambda role: "test-only-exchange-key")
    obj = AbantetherConnection(vault, history, ["BTCUSDT"])
    obj.state.update(
        status="connected",
        account_connected=True,
        updated_at=time.time(),
        balances=[{"symbol": "USDT", "balance": "1000", "blocked": "0"}],
        quotes=[
            {
                "symbol": "BTCUSDT",
                "bid": 100,
                "ask": 100.1,
                "price": 100.05,
                "at": time.time(),
                "active": True,
                "buy_max": "500",
                "sell_max": "100",
            }
        ],
    )
    return obj


def decision(identity="unit-decision"):
    return {
        "id": identity,
        "at": time.time(),
        "symbol": "BTCUSDT",
        "target_fraction": 0.2,
    }


@pytest.mark.asyncio
async def test_unknown_post_is_durable_and_never_retried(tmp_path):
    conn = connection(tmp_path)
    writes = []

    def handle(request):
        if "portfolio-balance" in str(request.url):
            return httpx.Response(200, json=conn.state["balances"])
        if request.method == "GET":
            return httpx.Response(200, json=[])
        writes.append(json.loads(request.content))
        raise httpx.ReadTimeout("test response lost")

    transport = httpx.MockTransport(handle)
    settings = AgentSettings(execution_mode="live", live_execution_enabled=True)
    executor = FundedExecution(tmp_path, conn, settings, transport=transport)
    item = decision()
    conn.history.record_decision(item)
    with pytest.raises(ValueError, match="unknown"):
        await executor.target(item, allow_entry=True)
    restored = FundedExecution(tmp_path, conn, settings, transport=transport)
    with pytest.raises(ValueError, match="unresolved"):
        await restored.target(decision("second"), allow_entry=True)
    assert len(writes) == 1
    assert writes[0]["volume"] == "25"
    assert conn.history.detail(writes[0]["track_id"])["id"] == item["id"]
    assert conn.history.trades("broker")["total"] == 0


@pytest.mark.asyncio
async def test_disabled_or_stale_account_never_submits(tmp_path):
    conn = connection(tmp_path)

    def no_requests(request):
        raise AssertionError("no outbound request expected")

    executor = FundedExecution(
        tmp_path, conn, AgentSettings(), transport=httpx.MockTransport(no_requests)
    )
    with pytest.raises(ValueError, match="disabled"):
        await executor.target(decision(), allow_entry=True)


@pytest.mark.asyncio
async def test_ack_is_not_a_fill_and_terminal_partial_reconciles_once(tmp_path):
    conn = connection(tmp_path)
    writes = []
    replies = []

    def handle(request):
        if "portfolio-balance" in str(request.url):
            return httpx.Response(200, json=conn.state["balances"])
        if request.method == "GET":
            return httpx.Response(200, json=replies)
        body = json.loads(request.content)
        writes.append(body)
        return httpx.Response(
            201,
            json={
                "id": "real-contract-test-order",
                "code": "ok",
                "base_symbol": "BTC",
                "quote_symbol": "USDT",
                "side": "buy",
                "type": "market",
                "status": "new",
                "volume": {"USDT": "25"},
                "filled_volume": {},
                "created_at": "2026-10-25T00:00:00Z",
            },
        )

    executor = FundedExecution(
        tmp_path,
        conn,
        AgentSettings(execution_mode="live", live_execution_enabled=True),
        transport=httpx.MockTransport(handle),
    )
    item = decision()
    conn.history.record_decision(item)
    receipt = await executor.target(item, allow_entry=True)
    assert receipt["status"] == "acknowledged"
    assert conn.history.trades("broker")["total"] == 0
    replies.append(
        {
            "track_id": writes[0]["track_id"],
            "id": "real-contract-test-order",
            "code": "ok",
            "base_symbol": "BTC",
            "quote_symbol": "USDT",
            "side": "buy",
            "type": "market",
            "status": "canceled",
            "volume": {"USDT": "25"},
            "filled_volume": {"BTC": "0.1"},
            "price": "100",
            "created_at": "2026-10-25T00:00:00Z",
        }
    )
    await executor.reconcile()
    await executor.reconcile()
    fills = conn.history.trades("broker")
    assert fills["total"] == 1
    assert fills["items"][0]["quantity"] == "0.1"
    assert fills["items"][0]["status"] == "terminal_partial_fill"
    assert executor.state["costs"]["BTCUSDT"] == "100"
    assert len(writes) == 1
    executor.settings = AgentSettings(
        execution_mode="live", live_execution_enabled=True
    )
    conn.state["updated_at"] = 0
    with pytest.raises(ValueError, match="fresh"):
        await executor.target(decision(), allow_entry=True)
