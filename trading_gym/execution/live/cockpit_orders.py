"""Abantether spot execution with durable intents and ambiguous-result quarantine.

No retry of a POST is permitted. Only a broker read can resolve an intent.
This component never receives model credentials or changes exchange credentials.
"""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from filelock import FileLock

from trading_gym.core.persistence import atomic_write_json, read_json
from trading_gym.market.adapters.enhanced_aban_client import Balance, OrderResponse

ZERO = Decimal(0)


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("invalid nonnegative broker value")
    return result


class FundedExecution:
    def __init__(self, root, connection, settings, *, transport=None):
        self.path = root / "funded-execution.json"
        self.connection, self.settings, self.transport = connection, settings, transport
        self.lock = asyncio.Lock()
        self.owner = FileLock(str(self.path) + ".owner.lock", timeout=0)
        self.state = read_json(
            self.path,
            {
                "intents": {},
                "gross_submitted": "0",
                "halted": False,
                "costs": {},
                "cooldowns": {},
            },
        )

    def persist(self):
        atomic_write_json(self.path, self.state)

    def valuation(self, *, require_basis=True):
        source = self.connection.state
        if (
            source["status"] != "connected"
            or not source["account_connected"]
            or time.time() - source.get("updated_at", 0)
            > self.settings.max_feed_age_seconds
        ):
            raise ValueError("fresh authenticated broker account is required")
        quotes = {q["symbol"]: q for q in source["quotes"]}
        balances = {row["symbol"]: row for row in source["balances"]}
        if "USDT" not in balances:
            raise ValueError("USDT account balance is required")
        equity = ZERO
        for symbol, row in balances.items():
            quantity = decimal(row["balance"])
            if decimal(row["blocked"]) > quantity:
                raise ValueError("blocked balance exceeds reported total")
            if not quantity:
                continue
            if symbol == "USDT":
                equity += quantity
                continue
            if require_basis and symbol + "USDT" not in self.state["costs"]:
                raise ValueError(
                    "existing holdings need a confirmed robot cost basis before funded operation"
                )
            quote = quotes.get(symbol + "USDT")
            if (
                not quote
                or time.time() - quote["at"] > self.settings.max_feed_age_seconds
            ):
                raise ValueError(
                    "all nonzero account assets require fresh valuation quotes"
                )
            equity += quantity * decimal(quote["bid"])
        if equity <= 0:
            raise ValueError("positive broker equity is required")
        return equity, balances, quotes

    async def reconcile(self):
        for identity, intent in self.state["intents"].items():
            if intent["status"] in {"filled", "rejected", "failed", "canceled"}:
                continue
            async with httpx.AsyncClient(
                timeout=12,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                payload = await self.connection._get(
                    client,
                    "https://api.abantether.com/order_handler/orders/otc",
                    self.connection.vault.key("abantether"),
                    {"track_id": identity, "per_page": 100, "page": 1},
                )
            rows = (
                payload.get("results", payload.get("orders"))
                if isinstance(payload, dict)
                else payload
            )
            if not isinstance(rows, list):
                raise TypeError("order reconciliation schema unavailable")
            matches = [raw for raw in rows if raw.get("track_id") == identity]
            if len(matches) > 1:
                raise ValueError(
                    "duplicate broker tracking identity; account review required"
                )
            if not matches:
                continue  # Absence is not proof that a timed-out POST failed.
            order = OrderResponse.model_validate(matches[0])
            if (
                order.base_symbol + order.quote_symbol != intent["symbol"]
                or order.side != intent["side"]
            ):
                raise ValueError("broker reconciliation does not match durable intent")
            intent["status"], intent["broker_order_id"] = order.status, order.id
            quantity = decimal(order.filled_volume.get(order.base_symbol, 0))
            if order.status == "filled" and not quantity:
                raise ValueError("broker reports filled order without filled quantity")
            if order.status in {"filled", "canceled", "failed"} and quantity:
                price = decimal(
                    order.price or order.coin_price or order.usdt_price or 0
                )
                if not quantity or not price:
                    raise ValueError("confirmed fill values are missing")
                if intent["side"] == "buy":
                    old_units = decimal(
                        self.state.setdefault("managed_units", {}).get(
                            intent["symbol"], 0
                        )
                    )
                    old_cost = decimal(self.state["costs"].get(intent["symbol"], price))
                    self.state["costs"][intent["symbol"]] = str(
                        (old_units * old_cost + quantity * price)
                        / (old_units + quantity)
                    )
                    self.state["managed_units"][intent["symbol"]] = str(
                        old_units + quantity
                    )
                else:
                    remaining = max(
                        ZERO,
                        decimal(
                            self.state.setdefault("managed_units", {}).get(
                                intent["symbol"], 0
                            )
                        )
                        - quantity,
                    )
                    self.state["managed_units"][intent["symbol"]] = str(remaining)
                    if not remaining:
                        self.state["costs"].pop(intent["symbol"], None)
                self.connection.history.execution(
                    {
                        "id": "abantether:" + order.id,
                        "broker_order_id": order.id,
                        "at": order.finalized_at or order.created_at,
                        "symbol": intent["symbol"],
                        "side": intent["side"],
                        "quantity": str(quantity),
                        "price": str(price),
                        "fee": None,
                        "realized_pnl": None,
                        "reason": intent["reason"],
                        "mode": "broker",
                        "decision_id": intent["decision_id"],
                        "attribution": "robot",
                        "status": (
                            "broker_confirmed"
                            if order.status == "filled"
                            else "terminal_partial_fill"
                        ),
                    }
                )
            self.persist()

    async def target(self, decision, *, allow_entry):
        async with self.lock:
            with self.owner:
                self.state = read_json(self.path, self.state)
                if (
                    not self.settings.live_execution_enabled
                    or not self.connection.vault.key("abantether")
                ):
                    raise ValueError(
                        "funded execution is disabled or lacks credentials"
                    )
                self.valuation(require_basis=False)
                await self.reconcile()
                if any(
                    i["status"] not in {"filled", "rejected", "failed", "canceled"}
                    for i in self.state["intents"].values()
                ):
                    raise ValueError(
                        "unresolved broker intent; reconcile before further orders"
                    )
                async with httpx.AsyncClient(
                    timeout=12,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport,
                ) as client:
                    payload = await self.connection._get(
                        client,
                        "https://mono.abantether.com/portfolio/portfolio-balance/",
                        self.connection.vault.key("abantether"),
                    )
                if not isinstance(payload, list):
                    raise ValueError("fresh balance schema unavailable")
                parsed = [Balance.model_validate(row) for row in payload]
                self.connection.state["balances"] = [
                    row.model_dump(mode="json") for row in parsed
                ]
                self.connection.state["account_updated_at"] = time.time()
                equity, balances, quotes = self.valuation()
                symbol = decision["symbol"]
                quote = quotes[symbol]
                asset = symbol.removesuffix("USDT")
                row = balances.get(asset, {"balance": 0, "blocked": 0})
                units = decimal(row["balance"])
                available = max(ZERO, units - decimal(row["blocked"]))
                cash = max(
                    ZERO,
                    decimal(balances["USDT"]["balance"])
                    - decimal(balances["USDT"]["blocked"]),
                )
                bid, ask = decimal(quote["bid"]), decimal(quote["ask"])
                if not bid or ask < bid or not quote.get("active", False):
                    raise ValueError("market is inactive or spread is invalid")
                today = datetime.now(UTC).date().isoformat()
                self.state.setdefault("peak", str(equity))
                self.state["peak"] = str(max(equity, decimal(self.state["peak"])))
                if self.state.get("day") != today:
                    self.state.update(day=today, day_open=str(equity))
                breached = 1 - equity / decimal(self.state["peak"]) >= Decimal(
                    str(self.settings.max_drawdown)
                ) or 1 - equity / decimal(self.state["day_open"]) >= Decimal(
                    str(self.settings.max_daily_loss)
                )
                if breached:
                    self.state["halted"] = True
                stop = symbol in self.state["costs"] and bid <= decimal(
                    self.state["costs"][symbol]
                ) * (1 - Decimal(str(self.settings.stop_loss_fraction)))
                if stop:
                    self.state["cooldowns"][symbol] = time.time() + 3600
                fraction = min(
                    decimal(decision["target_fraction"]),
                    Decimal(str(self.settings.max_position_fraction)),
                )
                if (
                    self.state["halted"]
                    or stop
                    or time.time() < self.state["cooldowns"].get(symbol, 0)
                ):
                    fraction = ZERO
                desired = equity * fraction
                if not allow_entry:
                    desired = min(desired, units * bid)
                delta = desired - units * bid
                if delta > 0 and (not allow_entry or self.state["halted"]):
                    return None
                if abs(delta) < Decimal(5):
                    self.persist()
                    return None
                side = "buy" if delta > 0 else "sell"
                notional = min(
                    abs(delta), Decimal(str(self.settings.live_max_order_usdt))
                )
                if side == "buy":
                    if (ask - bid) / bid > Decimal(
                        str(self.settings.live_max_spread_bps)
                    ) / 10000:
                        raise ValueError("spread exceeds funded execution limit")
                    notional = min(notional, cash / Decimal("1.02"))
                    volume = notional
                else:
                    volume = min(available, notional / bid)
                    notional = volume * bid
                if notional < decimal(quote.get("minimum_usdt", 5)):
                    raise ValueError("order is below broker minimum")
                maximum = decimal(
                    quote.get("buy_max" if side == "buy" else "sell_max", 0)
                )
                if not maximum or volume > maximum:
                    raise ValueError("order exceeds broker advertised volume limit")
                if side == "buy" and decimal(
                    self.state.get("buy_submitted", 0)
                ) + notional > Decimal(str(self.settings.live_total_budget_usdt)):
                    raise ValueError("funded submission budget exhausted")
                identity = (
                    "tg-" + hashlib.sha256(decision["id"].encode()).hexdigest()[:40]
                )
                if identity in self.state["intents"]:
                    return self.state["intents"][identity]
                body = {
                    "side": side,
                    "base_symbol": asset,
                    "quote_symbol": "USDT",
                    "volume": str(volume),
                    "track_id": identity,
                }
                intent = {
                    "status": "submitting",
                    "symbol": symbol,
                    "side": side,
                    "decision_id": decision["id"],
                    "reason": (
                        "stop_loss"
                        if stop
                        else "risk_halt" if self.state["halted"] else "strategy"
                    ),
                    "submitted_at": time.time(),
                    "body": body,
                    "risk": {
                        "account_equity": str(equity),
                        "peak": self.state["peak"],
                        "day_open": self.state["day_open"],
                        "drawdown": str(
                            max(ZERO, 1 - equity / decimal(self.state["peak"]))
                        ),
                        "daily_loss": str(
                            max(ZERO, 1 - equity / decimal(self.state["day_open"]))
                        ),
                        "position_cap": self.settings.max_position_fraction,
                        "stop_loss": stop,
                        "allow_entry": allow_entry,
                        "asof": time.time(),
                    },
                }
                self.state["intents"][identity] = intent
                self.connection.history.bind_broker_intent(identity, decision["id"])
                self.state["gross_submitted"] = str(
                    decimal(self.state["gross_submitted"]) + notional
                )
                if side == "buy":
                    self.state["buy_submitted"] = str(
                        decimal(self.state.get("buy_submitted", 0)) + notional
                    )
                self.persist()  # Commit BEFORE network I/O; a crash quarantines this identity.
                try:
                    async with httpx.AsyncClient(
                        timeout=12,
                        follow_redirects=False,
                        trust_env=False,
                        transport=self.transport,
                    ) as client, client.stream(
                        "POST",
                        "https://api.abantether.com/order_handler/orders/otc/market",
                        headers={
                            "Authorization": self.connection.vault.key("abantether")
                        },
                        json=body,
                    ) as response:
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 256000:
                                raise ValueError("broker reply exceeds limit")
                        if response.status_code not in {200, 201, 202}:
                            raise ValueError("broker submission was not acknowledged")
                        payload = json.loads(data)
                        order = OrderResponse.model_validate(
                            payload.get("data", payload)
                        )
                        if (
                            order.base_symbol + order.quote_symbol != symbol
                            or order.side != side
                        ):
                            raise ValueError(
                                "broker acknowledgement does not match submitted intent"
                            )
                        intent.update(status="acknowledged", broker_order_id=order.id)
                except (httpx.HTTPError, ValueError, TypeError, KeyError):
                    intent["status"] = "unknown"
                    self.persist()
                    raise ValueError(
                        "order outcome unknown; POST will not be retried"
                    ) from None
                self.persist()
                return intent
