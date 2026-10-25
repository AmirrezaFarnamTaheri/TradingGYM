"""Disposable in-memory storage for isolated shadow evaluation only.

Accounting transitions still use PortfolioLedger's Decimal and hash-chain code.
No live execution or restart recovery is offered by this storage backend.
"""

from copy import deepcopy
from threading import RLock
from pathlib import Path

from trading_gym.agent.experience_memory import ExperienceMemory
from trading_gym.execution.accounting.ledger import PortfolioLedger


class EvaluationLedger(PortfolioLedger):
    def __init__(self):
        super().__init__(
            "autonomous-paper", path=Path("__evaluation_only__"), audit_enabled=False
        )
        self.lock = RLock()
        self.document = self._empty()

    def _load(self):
        return deepcopy(self.document)

    def _write(self, document):
        self.document = deepcopy(document)


class EvaluationHistory:
    def __init__(self):
        self.records = {}
        self.fills = {}

    def record_decision(self, decision):
        self.records.setdefault(decision["id"], deepcopy(decision))

    def execution(self, trade):
        self.fills.setdefault(trade["id"], deepcopy(trade))


class EvaluationMemory(ExperienceMemory):
    def observations(self):
        return sum(entry.support for entry in self.entries())
