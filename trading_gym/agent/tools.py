"""Curated, bounded agent tools with no direct live-trade or code-execution authority.

Beyond the curated catalogue, the registry is *revisable* (ADR-034): every
invocation is metered, tools can be superseded or retired at runtime with a
journaled reason, and the agent can nominate tools it does not have. The
three surfaces:

- **Usage metering.** :meth:`SafeToolRegistry.usage_report` returns per-tool
  call counts, failure counts, and the last error — the evidence an agent
  needs to argue that a tool is broken or unused rather than asserting it.
- **Revision.** :meth:`revise` replaces a tool's handler and description
  (bumping its version), :meth:`retire` makes a tool refuse to run with a
  stated reason. Both append to an in-process revision log, and a retired
  tool disappears from :meth:`describe` so callers cannot select it.
- **Nomination.** :meth:`nominate` records a request for a tool that does
  not exist; nominations carry no authority and are surfaced for human
  absorption (the durable, hash-chained queue is the revision ledger in
  ``agent/revision.py``).

Every tool remains advisory or proposal-only: nothing here reaches the venue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from trading_gym.strategy_lab.templates import template_catalogue
from trading_gym.strategy_lab.validation import validate_strategy_spec

__all__ = [
    "SafeToolRegistry",
    "ToolDefinition",
    "ToolRequest",
    "ToolRetired",
    "ToolUsage",
]


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="agent", min_length=1, max_length=120)


class ToolRetired(RuntimeError):
    """The tool exists in history but was retired and must not run."""


@dataclass
class ToolUsage:
    """Per-tool meter: the evidence behind a revision argument."""

    calls: int = 0
    failures: int = 0
    last_error: str | None = None
    last_called_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "last_error": self.last_error,
            "last_called_at": self.last_called_at,
        }


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: Callable[[dict[str, Any], str], Any]
    version: int = 1
    retired_reason: str | None = None
    tags: tuple[str, ...] = field(default=())

    @property
    def retired(self) -> bool:
        return self.retired_reason is not None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        self._usage: dict[str, ToolUsage] = {}
        self._revisions: list[dict[str, Any]] = []
        self._nominations: list[dict[str, Any]] = []
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
            "strategy.templates",
            "List the curated declarative strategy families the forge can seed from.",
            self._list_templates,
        )
        self.register(
            "strategy.propose_variants",
            "Derive grammar-bounded parameter variants of a strategy; proposes only.",
            self._propose_variants,
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
            lambda args, actor: [
                item.model_dump(mode="json") for item in self.experiments.list()
            ],
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
        self.register(
            "tool.usage_report",
            "Report per-tool call counts, failures, and last error.",
            lambda args, actor: self.usage_report(),
        )
        self.register(
            "tool.revisions",
            "List runtime tool revisions and retirements with their reasons.",
            lambda args, actor: list(self.revisions()),
        )
        self.register(
            "tool.nominate",
            "Nominate a tool the registry lacks; records a request, grants nothing.",
            self._nominate_tool,
        )

    # -- registry surface ------------------------------------------------------

    def register(
        self,
        name: str,
        description: str,
        handler: Callable[[dict[str, Any], str], Any],
        *,
        tags: tuple[str, ...] = (),
    ) -> None:
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = ToolDefinition(
            name, description, handler, version=1, tags=tags
        )
        self._usage.setdefault(name, ToolUsage())

    def revise(
        self,
        name: str,
        *,
        description: str,
        handler: Callable[[dict[str, Any], str], Any],
        reason: str,
        actor: str = "operator",
    ) -> ToolDefinition:
        """Replace a tool's implementation, bumping its version.

        Reviving a retired tool is an explicit revision, so the reason is
        recorded and the retirement is lifted rather than shadowed.
        """

        existing = self._tools.get(name)
        if existing is None:
            raise KeyError(name)
        if not reason.strip():
            raise ValueError("a tool revision requires a reason")
        revised = ToolDefinition(
            name=name,
            description=description,
            handler=handler,
            version=existing.version + 1,
            retired_reason=None,
            tags=existing.tags,
        )
        self._tools[name] = revised
        self._log_revision(
            action="revised",
            name=name,
            version=revised.version,
            reason=reason,
            actor=actor,
        )
        return revised

    def retire(
        self, name: str, *, reason: str, actor: str = "operator"
    ) -> ToolDefinition:
        """Make a tool refuse to run and vanish from ``describe``."""

        existing = self._tools.get(name)
        if existing is None:
            raise KeyError(name)
        if not reason.strip():
            raise ValueError("retiring a tool requires a reason")
        retired = ToolDefinition(
            name=name,
            description=existing.description,
            handler=existing.handler,
            version=existing.version,
            retired_reason=reason.strip()[:500],
            tags=existing.tags,
        )
        self._tools[name] = retired
        self._log_revision(
            action="retired",
            name=name,
            version=retired.version,
            reason=reason,
            actor=actor,
        )
        return retired

    def nominate(
        self, *, tool_name: str, rationale: str, actor: str = "agent"
    ) -> dict[str, Any]:
        """Record a request for a tool the registry does not have."""

        if not tool_name.strip() or not rationale.strip():
            raise ValueError("a nomination requires a tool name and rationale")
        if tool_name in self._tools and not self._tools[tool_name].retired:
            raise ValueError(f"tool already available: {tool_name}")
        entry = {
            "tool_name": tool_name.strip()[:120],
            "rationale": rationale.strip()[:2000],
            "proposed_by": actor,
            "at": _now(),
            "status": "nominated",
        }
        self._nominations.append(entry)
        return dict(entry)

    def nominations(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(entry) for entry in self._nominations)

    def revisions(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(entry) for entry in self._revisions)

    def usage_report(self) -> dict[str, dict[str, Any]]:
        return {name: usage.to_dict() for name, usage in sorted(self._usage.items())}

    def describe(self) -> list[dict[str, str]]:
        """Callable tools only — retired tools are not selectable."""

        return [
            {
                "name": item.name,
                "description": item.description,
                "version": str(item.version),
            }
            for item in sorted(self._tools.values(), key=lambda item: item.name)
            if not item.retired
        ]

    def invoke(self, request: ToolRequest) -> Any:
        bounded_json_dumps(request.arguments, max_bytes=128 * 1024)
        definition = self._tools.get(request.tool)
        if definition is None:
            raise KeyError(request.tool)
        usage = self._usage.setdefault(request.tool, ToolUsage())
        if definition.retired:
            usage.failures += 1
            usage.last_error = f"retired: {definition.retired_reason}"
            raise ToolRetired(
                f"tool {request.tool!r} was retired: {definition.retired_reason}"
            )
        usage.calls += 1
        usage.last_called_at = _now()
        try:
            result = definition.handler(request.arguments, request.actor)
        except Exception as exc:
            usage.failures += 1
            # Error messages may embed provider URLs, account payloads,
            # API keys or user-authored content. Meter by exception type only:
            # the full exception still propagates to the authorized caller.
            usage.last_error = type(exc).__name__
            raise
        return redact_secrets(result)

    def _log_revision(
        self, *, action: str, name: str, version: int, reason: str, actor: str
    ) -> None:
        self._revisions.append(
            {
                "action": action,
                "tool": name,
                "version": version,
                "reason": reason.strip()[:500],
                "actor": actor,
                "at": _now(),
            }
        )

    # -- handlers --------------------------------------------------------------

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

    @staticmethod
    def _list_templates(args: dict[str, Any], actor: str) -> dict[str, Any]:
        catalogue = template_catalogue()
        detail = bool(args.get("detail", False))
        if detail:
            return {
                key: spec.model_dump(mode="json") for key, spec in catalogue.items()
            }
        return {
            key: {
                "name": spec.name,
                "timeframe": spec.timeframe,
                "tags": list(spec.tags),
                "indicators": [item.name for item in spec.indicators],
            }
            for key, spec in catalogue.items()
        }

    @staticmethod
    def _propose_variants(args: dict[str, Any], actor: str) -> dict[str, Any]:
        """Grammar-bounded variants of a spec; nothing is saved or applied."""

        from trading_gym.agent.strategy_variants import propose_variants

        spec = StrategySpec.model_validate(args.get("spec"))
        count = int(args.get("count", 3))
        seed = int(args.get("seed", 0))
        variants = propose_variants(spec, count=count, seed=seed)
        return {
            "seed": seed,
            "requested": count,
            "variants": [variant.model_dump(mode="json") for variant in variants],
        }

    def _nominate_tool(self, args: dict[str, Any], actor: str) -> dict[str, Any]:
        return self.nominate(
            tool_name=str(args["tool_name"]),
            rationale=str(args["rationale"]),
            actor=actor,
        )

    def _resolve_instrument(self, args: dict[str, Any], actor: str) -> dict[str, Any]:
        return self.instruments.resolve(
            str(args["identifier"]),
            venue=str(args["venue"]) if args.get("venue") is not None else None,
        ).model_dump(mode="json")

    @staticmethod
    def _size_position(args: dict[str, Any], actor: str) -> dict[str, Any]:
        return size_position(PositionSizingRequest.model_validate(args)).model_dump(
            mode="json"
        )

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
        return self.workflows.plan_backtest(request, actor=actor).model_dump(
            mode="json"
        )

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
