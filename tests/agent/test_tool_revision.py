"""AXIOM-1 revisable tool registry (ADR-034).

The registry was a fixed catalogue; it is now a *revisable* surface, and
these are the properties that make revision safe rather than merely
possible:

- **Metering.** Every invocation counts, failures count separately, and
  the last error is retained — the evidence an agent must cite to argue a
  tool is broken, instead of asserting it.
- **Revision.** ``revise`` swaps a handler and bumps the version;
  ``retire`` makes a tool refuse to run and removes it from ``describe``
  so it cannot be selected. Both demand a reason and are journaled.
- **Nomination.** A tool the registry lacks can be *requested* and
  nothing more: nomination grants no capability, and nominating something
  that already exists is an error.
- **Authority is unchanged.** No amount of revision creates a
  trade-execution tool, and results stay redacted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trading_gym.agent.tools import (
    SafeToolRegistry,
    ToolRequest,
    ToolRetired,
)
from trading_gym.governance.ledger import AuditLedger
from trading_gym.governance.service import GovernanceService
from trading_gym.strategy_lab.models import StrategySpec
from trading_gym.strategy_lab.repository import StrategyRepository
from trading_gym.strategy_lab.templates import template_catalogue


def build_registry(tmp_path: Path) -> SafeToolRegistry:
    governance = GovernanceService(ledger=AuditLedger(path=tmp_path / "audit.jsonl"))
    return SafeToolRegistry(
        strategies=StrategyRepository(tmp_path / "strategies.json"),
        governance=governance,
    )


class TestUsageMetering:
    def test_calls_are_counted(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.invoke(ToolRequest(tool="strategy.list", actor="agent", arguments={}))
        registry.invoke(ToolRequest(tool="strategy.list", actor="agent", arguments={}))
        report = registry.usage_report()
        assert report["strategy.list"]["calls"] == 2
        assert report["strategy.list"]["failures"] == 0
        assert report["strategy.list"]["last_called_at"]

    def test_failures_are_counted_and_the_error_retained(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        with pytest.raises(Exception):
            registry.invoke(
                ToolRequest(
                    tool="strategy.validate", actor="agent", arguments={"spec": {}}
                )
            )
        entry = registry.usage_report()["strategy.validate"]
        assert entry["calls"] == 1
        assert entry["failures"] == 1
        assert entry["last_error"]

    def test_usage_errors_do_not_leak_tool_exception_payload(
        self, tmp_path: Path
    ) -> None:
        registry = build_registry(tmp_path)
        private_message = "BROKER_API_KEY=do-not-display-this-token"

        def broken_tool(args, actor):
            raise RuntimeError(private_message)

        registry.register("research.broken", "Offline test", broken_tool)
        with pytest.raises(RuntimeError, match="BROKER_API_KEY"):
            registry.invoke(
                ToolRequest(tool="research.broken", actor="agent", arguments={})
            )
        report = registry.usage_report()["research.broken"]
        assert report["calls"] == 1
        assert report["failures"] == 1
        assert report["last_error"] == "RuntimeError"
        assert private_message not in str(registry.usage_report())

    def test_untouched_tools_report_zero(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        report = registry.usage_report()
        assert report["proposal.create"] == {
            "calls": 0,
            "failures": 0,
            "last_error": None,
            "last_called_at": None,
        }

    def test_the_report_is_reachable_as_a_tool(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        report = registry.invoke(
            ToolRequest(tool="tool.usage_report", actor="agent", arguments={})
        )
        assert report["tool.usage_report"]["calls"] == 1


class TestRevision:
    def test_revise_replaces_the_handler_and_bumps_the_version(
        self, tmp_path: Path
    ) -> None:
        registry = build_registry(tmp_path)
        registry.revise(
            "strategy.list",
            description="List strategies, newest first.",
            handler=lambda args, actor: ["revised"],
            reason="ordering was unstable for the UI",
            actor="operator-1",
        )
        result = registry.invoke(
            ToolRequest(tool="strategy.list", actor="agent", arguments={})
        )
        assert result == ["revised"]
        described = {item["name"]: item for item in registry.describe()}
        assert described["strategy.list"]["version"] == "2"
        assert described["strategy.list"]["description"].startswith("List strategies")

    def test_revision_is_journaled_with_its_reason(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.revise(
            "strategy.list",
            description="x",
            handler=lambda args, actor: [],
            reason="ordering was unstable",
            actor="operator-1",
        )
        entry = registry.revisions()[-1]
        assert entry["action"] == "revised"
        assert entry["tool"] == "strategy.list"
        assert entry["version"] == 2
        assert entry["reason"] == "ordering was unstable"
        assert entry["actor"] == "operator-1"
        assert entry["at"]

    def test_revision_requires_a_reason(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        with pytest.raises(ValueError):
            registry.revise(
                "strategy.list",
                description="x",
                handler=lambda args, actor: [],
                reason="   ",
            )

    def test_revising_an_unknown_tool_raises(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        with pytest.raises(KeyError):
            registry.revise(
                "nope.missing",
                description="x",
                handler=lambda args, actor: [],
                reason="r",
            )

    def test_registering_a_duplicate_name_raises(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        with pytest.raises(ValueError):
            registry.register("strategy.list", "duplicate", lambda args, actor: [])

    def test_revisions_are_reachable_as_a_tool(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.retire("strategy.list", reason="superseded", actor="operator-1")
        entries = registry.invoke(
            ToolRequest(tool="tool.revisions", actor="agent", arguments={})
        )
        assert entries[-1]["action"] == "retired"


class TestRetirement:
    def test_a_retired_tool_refuses_to_run(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.retire("strategy.list", reason="superseded by strategy.templates")
        with pytest.raises(ToolRetired):
            registry.invoke(
                ToolRequest(tool="strategy.list", actor="agent", arguments={})
            )

    def test_a_retired_tool_is_not_selectable(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.retire("strategy.list", reason="superseded")
        assert "strategy.list" not in {item["name"] for item in registry.describe()}

    def test_a_refused_call_is_metered_as_a_failure(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.retire("strategy.list", reason="superseded")
        with pytest.raises(ToolRetired):
            registry.invoke(
                ToolRequest(tool="strategy.list", actor="agent", arguments={})
            )
        entry = registry.usage_report()["strategy.list"]
        assert entry["calls"] == 0  # never ran
        assert entry["failures"] == 1
        assert "retired" in str(entry["last_error"])

    def test_retirement_requires_a_reason(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        with pytest.raises(ValueError):
            registry.retire("strategy.list", reason="")

    def test_revising_a_retired_tool_revives_it_explicitly(
        self, tmp_path: Path
    ) -> None:
        registry = build_registry(tmp_path)
        registry.retire("strategy.list", reason="superseded")
        registry.revise(
            "strategy.list",
            description="restored",
            handler=lambda args, actor: ["back"],
            reason="the replacement regressed",
            actor="operator-1",
        )
        assert registry.invoke(
            ToolRequest(tool="strategy.list", actor="agent", arguments={})
        ) == ["back"]
        assert "strategy.list" in {item["name"] for item in registry.describe()}
        assert [entry["action"] for entry in registry.revisions()] == [
            "retired",
            "revised",
        ]


class TestNomination:
    def test_a_missing_tool_can_be_nominated(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        entry = registry.invoke(
            ToolRequest(
                tool="tool.nominate",
                actor="agent-axiom1",
                arguments={
                    "tool_name": "funding.rate_monitor",
                    "rationale": "funding spikes preceded both stress exits",
                },
            )
        )
        assert entry["status"] == "nominated"
        assert entry["proposed_by"] == "agent-axiom1"
        assert registry.nominations()[0]["tool_name"] == "funding.rate_monitor"

    def test_nomination_grants_nothing(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.nominate(
            tool_name="funding.rate_monitor", rationale="r", actor="agent"
        )
        assert "funding.rate_monitor" not in {
            item["name"] for item in registry.describe()
        }
        with pytest.raises(KeyError):
            registry.invoke(
                ToolRequest(tool="funding.rate_monitor", actor="agent", arguments={})
            )

    def test_nominating_an_available_tool_is_rejected(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        with pytest.raises(ValueError):
            registry.nominate(tool_name="strategy.list", rationale="r", actor="agent")

    def test_a_retired_tool_may_be_re_nominated(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.retire("strategy.list", reason="superseded")
        entry = registry.nominate(
            tool_name="strategy.list",
            rationale="the replacement lost version history",
            actor="agent",
        )
        assert entry["status"] == "nominated"

    def test_nomination_needs_a_rationale(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        with pytest.raises(ValueError):
            registry.nominate(tool_name="x.y", rationale="  ", actor="agent")


class TestNewTools:
    def test_templates_tool_lists_the_catalogue(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        listing = registry.invoke(
            ToolRequest(tool="strategy.templates", actor="agent", arguments={})
        )
        assert set(listing) == set(template_catalogue())
        entry = listing["atr_volatility_breakout"]
        assert entry["timeframe"]
        assert "atr" in entry["indicators"]

    def test_templates_tool_can_return_full_specs(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        detailed = registry.invoke(
            ToolRequest(
                tool="strategy.templates", actor="agent", arguments={"detail": True}
            )
        )
        spec = StrategySpec.model_validate(detailed["sma_crossover"])
        assert spec.name == template_catalogue()["sma_crossover"].name

    def test_propose_variants_tool_is_deterministic_and_saves_nothing(
        self, tmp_path: Path
    ) -> None:
        registry = build_registry(tmp_path)
        payload: dict[str, Any] = {
            "spec": template_catalogue()["sma_crossover"].model_dump(mode="json"),
            "count": 3,
            "seed": 7,
        }
        first = registry.invoke(
            ToolRequest(
                tool="strategy.propose_variants", actor="agent", arguments=payload
            )
        )
        second = registry.invoke(
            ToolRequest(
                tool="strategy.propose_variants", actor="agent", arguments=payload
            )
        )
        assert first == second
        assert first["requested"] == 3
        assert len(first["variants"]) == 3
        for variant in first["variants"]:
            StrategySpec.model_validate(variant)  # inside the grammar
        assert registry.strategies.list() == []  # nothing persisted

    def test_propose_variants_rejects_an_unbounded_generation(
        self, tmp_path: Path
    ) -> None:
        registry = build_registry(tmp_path)
        with pytest.raises(ValueError):
            registry.invoke(
                ToolRequest(
                    tool="strategy.propose_variants",
                    actor="agent",
                    arguments={
                        "spec": template_catalogue()["sma_crossover"].model_dump(
                            mode="json"
                        ),
                        "count": 500,
                    },
                )
            )


class TestAuthorityIsUnchanged:
    def test_no_execution_tool_exists_after_revision(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.revise(
            "strategy.list",
            description="x",
            handler=lambda args, actor: [],
            reason="r",
        )
        names = {item["name"] for item in registry.describe()}
        assert not any(
            name.startswith(("trade.execute", "trade.live", "wallet."))
            for name in names
        )

    def test_results_are_still_redacted(self, tmp_path: Path) -> None:
        registry = build_registry(tmp_path)
        registry.revise(
            "strategy.list",
            description="leaky",
            handler=lambda args, actor: {"api_key": "super-secret-value"},
            reason="test the redaction seam",
        )
        result = registry.invoke(
            ToolRequest(tool="strategy.list", actor="agent", arguments={})
        )
        assert "super-secret-value" not in str(result)
