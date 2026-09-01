"""Curated, bounded agent tools with no direct live-trade or code-execution authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from trading_gym.common.security import bounded_json_dumps, redact_secrets
from trading_gym.convergence import BacktestWorkflowRequest, ConvergenceWorkflowService
from trading_gym.experiments import ExperimentCreate, ExperimentRepository
from trading_gym.governance.models import ProposalCreate, RiskTier
from trading_gym.governance.service import GovernanceService
from trading_gym.market import InstrumentRegistry
from trading_gym.research.dossier import build_dossier
from trading_gym.research.models import EvidenceItem, ResearchDossier
from trading_gym.research.quality import assess_dossier
from trading_gym.research.workspace import ResearchWorkspace
from trading_gym.risk.portfolio import (
    PortfolioRiskLimits,
    PortfolioSnapshot,
    evaluate_portfolio_risk,
)
from trading_gym.risk.sizing import PositionSizingRequest, size_position
from trading_gym.strategy_lab.models import StrategySpec
from trading_gym.strategy_lab.repository import StrategyRepository
from trading_gym.strategy_lab.validation import validate_strategy_spec


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="agent", min_length=1, max_length=120)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: Callable[[dict[str, Any], str], Any]


class SafeToolRegistry:
    def __init__(
        self,
        *,
        strategies: StrategyRepository | None = None,
        governance: GovernanceService | None = None,
        instruments: InstrumentRegistry | None = None,
        experiments: ExperimentRepository | None = None,
        workspace: ResearchWorkspace | None = None,
        workflows: ConvergenceWorkflowService | None = None,
    ) -> None:
        self.strategies = strategies or StrategyRepository()
        self.governance = governance or GovernanceService()
        self.instruments = instruments or InstrumentRegistry()
        self.experiments = experiments or ExperimentRepository()
        self.workspace = workspace or ResearchWorkspace()
        self.workflows = workflows or ConvergenceWorkflowService(
            strategies=self.strategies,
            instruments=self.instruments,
            experiments=self.experiments,
            ledger=self.governance.ledger,
        )
        self._tools: dict[str, ToolDefinition] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(
            "strategy.list",
            "List declarative strategy versions.",
            lambda args, actor: self.strategies.list(),
        )
        self.register(
            "strategy.save",
            "Validate and save a declarative strategy using revision CAS.",
            self._save_strategy,
        )
        self.register(
            "strategy.validate",
            "Statically validate strategy complexity, warm-up, risk, and backtest bounds.",
            self._validate_strategy,
        )
        self.register(
            "market.instrument.resolve",
            "Resolve a canonical instrument or venue alias.",
            self._resolve_instrument,
        )
        self.register(
            "risk.position_size",
            "Calculate a bounded position size without placing an order.",
            self._size_position,
        )
        self.register(
            "risk.portfolio_evaluate",
            "Evaluate portfolio exposure and loss gates without side effects.",
            self._evaluate_portfolio,
        )
        self.register(
            "research.dossier",
            "Assemble a deterministic evidence dossier.",
            self._build_dossier,
        )
        self.register(
            "research.quality",
            "Assess dossier coverage, freshness, citations, and evidence quality.",
            self._assess_research,
        )
        self.register(
            "research.workspace_snapshot",
            "Read the durable research watchlist, notes, and report index.",
            lambda args, actor: self.workspace.snapshot(),
        )
        self.register(
            "workflow.plan_backtest",
            "Validate a saved strategy and create a research-only backtest experiment plan.",
            self._plan_backtest,
        )
        self.register(
            "experiment.create",
            "Create a bounded research/backtest/discovery job; does not execute live trades.",
            self._create_experiment,
        )
        self.register(
            "experiment.list",
            "List durable experiment jobs.",
            lambda args, actor: [item.model_dump(mode="json") for item in self.experiments.list()],
        )
        self.register(
            "proposal.create",
            "Create a human-reviewable operation proposal; never executes the operation.",
            self._create_proposal,
        )
        self.register(
            "governance.audit_verify",
            "Verify the audit chain, HMAC seals, and head anchor.",
            lambda args, actor: self.governance.ledger.verify(),
        )

    def register(
        self,
        name: str,
        description: str,
        handler: Callable[[dict[str, Any], str], Any],
    ) -> None:
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = ToolDefinition(name, description, handler)

    def describe(self) -> list[dict[str, str]]:
        return [
            {"name": item.name, "description": item.description}
            for item in sorted(self._tools.values(), key=lambda item: item.name)
        ]

    def invoke(self, request: ToolRequest) -> Any:
        bounded_json_dumps(request.arguments, max_bytes=128 * 1024)
        definition = self._tools.get(request.tool)
        if definition is None:
            raise KeyError(request.tool)
        return redact_secrets(definition.handler(request.arguments, request.actor))

    def _save_strategy(self, args: dict[str, Any], actor: str) -> dict[str, Any]:
        spec = StrategySpec.model_validate(args.get("spec"))
        return self.strategies.save(
            spec,
            strategy_id=args.get("strategy_id"),
            expected_revision=args.get("expected_revision"),
            actor=actor,
        )

    @staticmethod
    def _validate_strategy(args: dict[str, Any], actor: str) -> dict[str, Any]:
        spec = StrategySpec.model_validate(args.get("spec"))
        return validate_strategy_spec(
            spec,
            market=args.get("market"),
            start=args.get("start"),
            end=args.get("end"),
        ).model_dump(mode="json")

    def _resolve_instrument(self, args: dict[str, Any], actor: str) -> dict[str, Any]:
        return self.instruments.resolve(
            str(args["identifier"]),
            venue=str(args["venue"]) if args.get("venue") is not None else None,
        ).model_dump(mode="json")

    @staticmethod
    def _size_position(args: dict[str, Any], actor: str) -> dict[str, Any]:
        return size_position(PositionSizingRequest.model_validate(args)).model_dump(mode="json")

    @staticmethod
    def _evaluate_portfolio(args: dict[str, Any], actor: str) -> dict[str, Any]:
        snapshot = PortfolioSnapshot.model_validate(args.get("snapshot"))
        limits = PortfolioRiskLimits.model_validate(args.get("limits", {}))
        return evaluate_portfolio_risk(snapshot, limits).model_dump(mode="json")

    @staticmethod
    def _build_dossier(args: dict[str, Any], actor: str) -> dict[str, Any]:
        items = [EvidenceItem.model_validate(item) for item in args.get("evidence", [])]
        return build_dossier(
            subject=str(args["subject"]),
            evidence=items,
        ).model_dump(mode="json")

    @staticmethod
    def _assess_research(args: dict[str, Any], actor: str) -> dict[str, Any]:
        dossier = ResearchDossier.model_validate(args.get("dossier"))
        return assess_dossier(
            dossier,
            freshness_days=int(args.get("freshness_days", 30)),
        ).model_dump(mode="json")

    def _plan_backtest(self, args: dict[str, Any], actor: str) -> dict[str, Any]:
        request = BacktestWorkflowRequest.model_validate(args)
        return self.workflows.plan_backtest(request, actor=actor).model_dump(mode="json")

    def _create_experiment(self, args: dict[str, Any], actor: str) -> dict[str, Any]:
        request = ExperimentCreate.model_validate(args)
        return self.experiments.create(request).model_dump(mode="json")

    def _create_proposal(self, args: dict[str, Any], actor: str) -> dict[str, Any]:
        request = ProposalCreate(
            operation=str(args["operation"]),
            summary=str(args["summary"]),
            payload=args.get("payload", {}),
            risk_tier=RiskTier(str(args.get("risk_tier", "medium"))),
            expires_in_seconds=int(args.get("expires_in_seconds", 900)),
            idempotency_key=str(args["idempotency_key"]),
        )
        return self.governance.create(request, actor=actor).model_dump(mode="json")
