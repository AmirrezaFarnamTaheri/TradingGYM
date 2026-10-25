"""Rebuild existing memory types from completed, verified paper account windows.

The PortfolioLedger remains the financial source. The DecisionJournal stores
immutable evidence references and availability times, not a second balance.
These labels concern an entire flat-to-flat account window; they are neither
isolated-signal causal profit nor verified live broker settlement.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Sequence

from trading_gym.execution.accounting.ledger import PortfolioLedger
from trading_gym.execution.accounting.models import AccountingEvent, AccountingEventType
from trading_gym.agent.experience_memory import ExperienceMemory
from trading_gym.agent.persistent_experience import MemoryAdvice, memory_advice
from trading_gym.agent.working_memory import WorkingMemory
from trading_gym.agent.intelligence.decision_journal import DecisionJournal, canonical_digest
from trading_gym.agent.intelligence.types import UncertainDecision

_TYPE = "closed_paper_account_window"
_EXTERNAL = {"external_capital_flow", "external_deposit", "external_withdrawal"}
_INTERNAL = {"simulation_cost_reconciliation"}


def _window_evidence(
    journal: DecisionJournal,
    ledger: PortfolioLedger,
    *,
    decision_id: str,
    opening_sequence: int,
    closing_sequence: int,
    quote_currency: str,
    available_at: float,
) -> dict:
    decision = journal.decision(decision_id)
    if (
        decision["mode"] != "paper"
        or decision["account_id"] != ledger.account_id
        or decision["status"] != "admitted"
    ):
        raise PermissionError("only admitted paper account windows can be projected")
    if (
        type(opening_sequence) is not int  # noqa: E721
        or type(closing_sequence) is not int  # noqa: E721
        or not 0 <= opening_sequence < closing_sequence
        or closing_sequence - opening_sequence > 512
        or type(available_at) not in (int, float)
        or not math.isfinite(available_at)
        or available_at < decision["observed_at"]
    ):
        raise ValueError("invalid bounded settlement window")
    # Read under the ledger's existing lock. Reuse its verified replay, not a
    # new accounting formula. These private read/replay seams are pinned to the
    # repository revision; no document is written or audit event emitted here.
    with ledger.lock:
        document = ledger._load()
        ledger._verify_chain_document(document)
    if closing_sequence > len(document["events"]):
        raise UncertainDecision("settlement window is incomplete")
    replay = ledger._empty()
    replay["opening_cash"] = dict(document["opening_cash"])
    replay["cash"] = dict(document["opening_cash"])
    opening = None
    external = Decimal("0")
    selected = []
    for sequence in range(closing_sequence + 1):
        if sequence:
            record = document["events"][sequence - 1]
            event = AccountingEvent.model_validate(record["payload"])
            ledger._apply_payload(replay, event)
            if sequence > opening_sequence:
                timestamp = event.occurred_at.timestamp()
                if not decision["observed_at"] <= timestamp <= available_at:
                    raise ValueError(
                        "settlement includes pre-decision or unavailable evidence"
                    )
                if event.event_type is AccountingEventType.FILL:
                    origin = journal.decision(
                        str(event.metadata.get("decision_id", ""))
                    )
                    if (
                        event.metadata.get("execution_authority") != "simulated"
                        or origin["mode"] != "paper"
                        or origin["account_id"] != ledger.account_id
                        or origin["status"] != "admitted"
                        or origin["observed_at"] > timestamp
                    ):
                        raise PermissionError(
                            "fill lacks linked simulated decision evidence"
                        )
                if event.event_type is AccountingEventType.CASH_ADJUSTMENT:
                    reason = event.metadata.get("reason")
                    if reason in _EXTERNAL:
                        if event.currency != quote_currency:
                            raise ValueError(
                                "cross-currency capital flow is not qualified"
                            )
                        external += event.amount
                    elif reason not in _INTERNAL:
                        raise PermissionError(
                            "cash adjustment is not classified as capital or cost"
                        )
                selected.append(record)
        if sequence in {opening_sequence, closing_sequence}:
            if any(Decimal(p["quantity"]) != 0 for p in replay["positions"].values()):
                raise UncertainDecision("account window is not flat and settled")
            if any(
                currency != quote_currency and Decimal(value) != 0
                for currency, value in replay["cash"].items()
            ) or any(
                p["quote_currency"] != quote_currency
                for p in replay["positions"].values()
            ):
                raise ValueError("only a single quote-currency account is qualified")
            cash = Decimal(replay["cash"].get(quote_currency, "0"))
            if sequence == opening_sequence:
                opening = cash
    assert opening is not None
    opening_head = (
        document["events"][opening_sequence - 1]["record_hash"]
        if opening_sequence
        else ledger._genesis_hash(document["opening_cash"])
    )
    closing_head = document["events"][closing_sequence - 1]["record_hash"]
    return {
        "settlement_type": _TYPE,
        "account_id": ledger.account_id,
        "mode": "paper",
        "quote_currency": quote_currency,
        "opening_sequence": opening_sequence,
        "closing_sequence": closing_sequence,
        "opening_chain_head": opening_head,
        "closing_chain_head": closing_head,
        "ledger_events_sha256": canonical_digest(selected),
        "event_count": len(selected),
        "opening_cash": str(opening),
        "closing_cash": str(cash),
        "external_capital_flow": str(external),
        "net_pnl_quote": str(cash - opening - external),
        "available_at": float(available_at),
        "causal_signal_attribution": False,
    }


def record_closed_paper_window(
    journal: DecisionJournal,
    ledger: PortfolioLedger,
    *,
    decision_id: str,
    opening_sequence: int,
    closing_sequence: int,
    quote_currency: str,
    available_at: float,
) -> str:
    """Derive a label; repeated calls reference the same immutable window."""
    payload = _window_evidence(
        journal,
        ledger,
        decision_id=decision_id,
        opening_sequence=opening_sequence,
        closing_sequence=closing_sequence,
        quote_currency=quote_currency,
        available_at=available_at,
    )
    identity = {
        key: payload[key]
        for key in (
            "account_id",
            "opening_chain_head",
            "closing_chain_head",
            "quote_currency",
        )
    }
    event_id = "paper-window-" + canonical_digest(identity)
    journal.append_event(
        decision_id=decision_id,
        event_id=event_id,
        kind="settlement",
        occurred_at=available_at,
        payload=payload,
    )
    return event_id


@dataclass(frozen=True)
class SettledMemoryProjection:
    working: WorkingMemory
    experiences: ExperienceMemory
    settlement_ids: tuple[str, ...]
    as_of: float

    def advice(
        self, tags: Sequence[str], *, k: int = 5, now: float | None = None
    ) -> MemoryAdvice:
        cutoff = self.as_of if now is None else min(now, self.as_of)
        return memory_advice(tags, self.experiences.query(list(tags), k=k, now=cutoff))


def rebuild_settled_memory(
    journal: DecisionJournal,
    ledger: PortfolioLedger,
    *,
    as_of: float,
    max_cycles: int = 64,
    max_entries: int = 500,
) -> SettledMemoryProjection:
    """Deterministic as-of projection; no append to a second memory journal."""
    working, experiences = WorkingMemory(max_cycles), ExperienceMemory(max_entries)
    admitted = {
        row["decision_id"]: row
        for row in journal.decisions(account_id=ledger.account_id, as_of=as_of)
    }
    windows, identifiers = [], []
    for event in journal.events(kind="settlement", as_of=as_of):
        payload = event["payload"]
        if (
            payload.get("settlement_type") != _TYPE
            or payload.get("account_id") != ledger.account_id
        ):
            continue  # a receipt or open position mark is not a closed P&L label
        decision = admitted.get(event["decision_id"])
        if decision is None:
            continue
        evidence = _window_evidence(
            journal,
            ledger,
            decision_id=event["decision_id"],
            opening_sequence=payload["opening_sequence"],
            closing_sequence=payload["closing_sequence"],
            quote_currency=payload["quote_currency"],
            available_at=payload["available_at"],
        )
        if evidence != payload:
            raise PermissionError("settlement label disagrees with financial ledger")
        start, end = payload["opening_sequence"], payload["closing_sequence"]
        if any(
            max(start, prior_start) < min(end, prior_end)
            for prior_start, prior_end in windows
        ):
            raise PermissionError(
                "overlapping account windows cannot inflate memory support"
            )
        windows.append((start, end))
        pnl = float(Decimal(payload["net_pnl_quote"]))
        outcome = "profitable" if pnl > 0 else "loss" if pnl < 0 else "neutral"
        source = decision["signal"]["source_id"] or decision["signal"]["backend"]
        summary = f"Paper account window {start}:{end}; not isolated-signal causal P&L"
        working.record_cycle(
            cycle_id=event["event_id"],
            regime="account_window",
            fused_summary=summary,
            invoked=(source,),
            benched=(),
        )
        working.settle(cycle_id=event["event_id"], outcome=outcome, pnl_quote=pnl)
        experiences.record(
            context_tags=["paper", "ledger_settlement", f"account:{ledger.account_id}"],
            outcome=outcome,
            lesson=f"{summary}; net {payload['net_pnl_quote']} {payload['quote_currency']} including costs",
            created_at=max(event["occurred_at"], event["recorded_at"]),
        )
        identifiers.append(event["event_id"])
    return SettledMemoryProjection(working, experiences, tuple(identifiers), as_of)
