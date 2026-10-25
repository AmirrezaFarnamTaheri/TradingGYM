"""Abantether read integration using the endpoint catalogue in its official SDK."""

import asyncio
import math
import time
from datetime import datetime

import httpx

from trading_gym.market.adapters.enhanced_aban_client import (
    Balance,
    OrderResponse,
    TickerData,
)


class AbantetherConnection:
    def __init__(self, vault, history, symbols):
        self.vault, self.history, self.symbols = vault, history, symbols
        self.state = {
            "venue": "abantether",
            "status": "not_connected",
            "updated_at": None,
            "quotes": [],
            "balances": [],
            "error": None,
            "account_connected": False,
        }
        self.bars = {s: history.bars(s) for s in symbols}
        self.pending = {}

    @staticmethod
    async def _get(client, url, key="", params=None):
        # No redirects: an account credential must never follow another host.
        async with client.stream(
            "GET", url, headers={"Authorization": key} if key else {}, params=params
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Abantether returned HTTP {response.status_code}")
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > 2_000_000:
                    raise ValueError("Abantether response exceeds the read limit")
        import json

        payload = json.loads(data)
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def refresh(self):
        key = self.vault.key("abantether")
        try:
            async with httpx.AsyncClient(
                timeout=12, follow_redirects=False, trust_env=False
            ) as client:
                payload = await self._get(
                    client, "https://mono.abantether.com/api/v1/otc/coin-price/", key
                )
                rows = (
                    payload
                    if isinstance(payload, list)
                    else (
                        [payload]
                        if isinstance(payload, dict) and "symbol" in payload
                        else list(payload.values()) if isinstance(payload, dict) else []
                    )
                )
                if not rows:
                    raise ValueError("Abantether did not provide quote records")
                quotes = []
                for item in rows:
                    quote = TickerData.model_validate(item)
                    symbol = (
                        quote.symbol
                        if quote.symbol.endswith("USDT")
                        else quote.symbol + "USDT"
                    )
                    if symbol not in self.symbols:
                        continue
                    price = float(quote.mid_price)
                    if not math.isfinite(price) or price <= 0:
                        raise ValueError("Invalid Abantether quote")
                    quotes.append(
                        {
                            "symbol": symbol,
                            "bid": float(quote.sell_price),
                            "ask": float(quote.buy_price),
                            "price": price,
                            "active": quote.active,
                            "buy_max": str(quote.buy_max),
                            "sell_max": str(quote.sell_max),
                            "spread_bps": float(
                                (quote.buy_price - quote.sell_price)
                                / quote.mid_price
                                * 10000
                            ),
                            "at": time.time(),
                        }
                    )
                if not quotes:
                    raise ValueError(
                        "Configured markets were absent from Abantether quotes"
                    )
                now = time.time()
                for quote in quotes:
                    bucket = int(now // 300) * 300
                    previous = self.pending.get(quote["symbol"])
                    # Publish only fully observed intervals, never fabricate OHLC history.
                    if (
                        previous
                        and previous["bucket"] < bucket
                        and previous["complete"]
                    ):
                        self.bars[quote["symbol"]].append(
                            (previous["bucket"] + 300, previous["price"])
                        )
                        self.history.bar(
                            quote["symbol"], previous["bucket"] + 300, previous["price"]
                        )
                        self.bars[quote["symbol"]] = self.bars[quote["symbol"]][-500:]
                    self.pending[quote["symbol"]] = {
                        "bucket": bucket,
                        "price": quote["price"],
                        "open": (
                            previous["open"]
                            if previous and previous["bucket"] == bucket
                            else quote["price"]
                        ),
                        "high": (
                            max(previous["high"], quote["price"])
                            if previous and previous["bucket"] == bucket
                            else quote["price"]
                        ),
                        "low": (
                            min(previous["low"], quote["price"])
                            if previous and previous["bucket"] == bucket
                            else quote["price"]
                        ),
                        "complete": (
                            previous["complete"]
                            if previous and previous["bucket"] == bucket
                            else previous is not None
                            and bucket - previous["bucket"] == 300
                        ),
                    }
                self.state.update(
                    {
                        "status": "connected",
                        "error": None,
                        "quotes": quotes,
                        "updated_at": now,
                        "bar_counts": {s: len(self.bars[s]) for s in self.symbols},
                        "pending_bars": self.pending.copy(),
                    }
                )
                if key:
                    balances = await self._get(
                        client,
                        "https://mono.abantether.com/portfolio/portfolio-balance/",
                        key,
                    )
                    if not isinstance(balances, list):
                        raise ValueError("Abantether balance schema was not recognized")
                    parsed = [Balance.model_validate(row) for row in balances]
                    if any(row.balance < 0 or row.blocked < 0 for row in parsed):
                        raise ValueError("Invalid broker balance")
                    self.state.update(
                        {
                            "balances": [row.model_dump(mode="json") for row in parsed],
                            "account_connected": True,
                        }
                    )
                    await self._orders(client, key)
                else:
                    self.state.update({"balances": [], "account_connected": False})
        except (httpx.HTTPError, RuntimeError, ValueError, TypeError, KeyError):
            # Preserve last observed prices with their timestamps, but never call
            # an old account response current after authentication/network failure.
            self.state.update(
                {
                    "status": "unavailable",
                    "account_connected": False,
                    "error": "Abantether connection unavailable. Check the API key, network, and broker response contract.",
                }
            )
        return self.state

    async def _orders(self, client, key):
        count = 0
        for page in range(1, 11):
            payload = await self._get(
                client,
                "https://api.abantether.com/order_handler/orders/otc",
                key,
                {"page": page, "per_page": 100},
            )
            rows = (
                payload.get("results", payload.get("orders"))
                if isinstance(payload, dict)
                else payload
            )
            if not isinstance(rows, list):
                raise TypeError("Abantether order history schema was not recognized")
            for raw in rows:
                order = OrderResponse.model_validate(raw)
                if order.status not in {"filled", "canceled", "failed"}:
                    continue
                quantity = order.filled_volume.get(order.base_symbol)
                if order.status != "filled" and (quantity is None or quantity == 0):
                    continue
                price = order.price or order.coin_price or order.usdt_price
                if quantity is None or quantity <= 0 or price is None or price <= 0:
                    raise ValueError(
                        "Filled broker order lacks confirmed execution values"
                    )
                datetime.fromisoformat(order.finalized_at or order.created_at)
                identity = raw.get("track_id")
                decision = (
                    self.history.detail(identity) if isinstance(identity, str) else None
                )
                self.history.execution(
                    {
                        "id": "abantether:" + order.id,
                        "broker_order_id": order.id,
                        "at": order.finalized_at or order.created_at,
                        "symbol": order.base_symbol + order.quote_symbol,
                        "side": order.side,
                        "quantity": str(quantity),
                        "price": str(price),
                        "fee": None,
                        "realized_pnl": None,
                        "reason": "robot" if decision else "broker_history",
                        "mode": "broker",
                        "decision_id": decision["id"] if decision else None,
                        "attribution": "robot" if decision else "unattributed",
                        "status": (
                            "broker_confirmed"
                            if order.status == "filled"
                            else "terminal_partial_fill"
                        ),
                    }
                )
                count += 1
            if len(rows) < 100:
                break
        self.state["history_updated_at"] = time.time()
        self.state["history_complete"] = len(rows) < 100
        self.state["confirmed_fills_read"] = count

    async def run(self):
        while True:
            await self.refresh()
            await asyncio.sleep(30)
