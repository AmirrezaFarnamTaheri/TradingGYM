"""Closed-world registry for reconstructed late-wave assurance evaluators.

The registry makes recovered convergence capabilities discoverable without
turning any of them into an authority surface. Every evaluator returns a
non-mutating :class:`AssuranceReport`; none can trade, pay, fetch credentials,
or mutate production state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .agent_architecture_profile_assurance import evaluate_agent_architecture
from .agent_mesh_routing_assurance import evaluate_agent_mesh_routing
from .agent_operator_projection_assurance import evaluate_operator_projection
from .agent_workspace_lease_assurance import evaluate_workspace_leases
from .alternative_market_evidence_assurance import evaluate_alternative_market_evidence
from .crypto_signal_pack_assurance import evaluate_crypto_signal_pack
from .data_provider_contract_assurance import evaluate_provider_contracts
from .embedded_widget_assurance import evaluate_embedded_widget
from .equity_research_assembly_assurance import evaluate_equity_research_assembly
from .finance_evaluation_campaign_assurance import evaluate_finance_evaluation_campaign
from .finance_skill_chain_assurance import evaluate_finance_skill_chain
from .financial_ui_projection_assurance import evaluate_financial_ui_projection
from .financial_workspace_assurance import evaluate_financial_workspace
from .forecast_experiment_comparison_assurance import evaluate_forecast_experiment_comparison
from .formal_harness_trace_assurance import evaluate_formal_harness_trace
from .harness_benchmark_assurance import evaluate_harness_benchmark
from .household_debt_stress_assurance import evaluate_household_debt_stress
from .investment_research_pipeline_assurance import evaluate_investment_research_pipeline
from .ledger_ingestion_plan_assurance import evaluate_ledger_ingestion_plan
from .mcp_runtime_profile_assurance import evaluate_mcp_runtime_profile
from .multi_stage_contest_assurance import evaluate_multi_stage_contest
from .multi_venue_trade_plan_assurance import evaluate_multi_venue_trade_plan
from .publication_profile_assurance import evaluate_publication_profile
from .terminal_command_admission_assurance import evaluate_terminal_command_admission
from .wallet_action_assurance import evaluate_wallet_action
from .assurance_contract import AssuranceReport

Evaluator = Callable[[Any], AssuranceReport]


@dataclass(frozen=True)
class ConvergenceAssuranceSpec:
    slug: str
    module: str
    evaluator_name: str
    evaluator: Evaluator
    authority: str = "advisory-only"
    mutation_capable: bool = False

    def public_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "module": self.module,
            "evaluator": self.evaluator_name,
            "authority": self.authority,
            "mutation_capable": self.mutation_capable,
        }


def _spec(slug: str, module: str, evaluator: Evaluator) -> ConvergenceAssuranceSpec:
    return ConvergenceAssuranceSpec(
        slug=slug,
        module=module,
        evaluator_name=evaluator.__name__,
        evaluator=evaluator,
    )


_SPECS = (
    _spec("agent-architecture-profile", "agent_architecture_profile_assurance", evaluate_agent_architecture),
    _spec("agent-mesh-routing", "agent_mesh_routing_assurance", evaluate_agent_mesh_routing),
    _spec("agent-operator-projection", "agent_operator_projection_assurance", evaluate_operator_projection),
    _spec("agent-workspace-lease", "agent_workspace_lease_assurance", evaluate_workspace_leases),
    _spec("alternative-market-evidence", "alternative_market_evidence_assurance", evaluate_alternative_market_evidence),
    _spec("crypto-signal-pack", "crypto_signal_pack_assurance", evaluate_crypto_signal_pack),
    _spec("data-provider-contract", "data_provider_contract_assurance", evaluate_provider_contracts),
    _spec("embedded-widget", "embedded_widget_assurance", evaluate_embedded_widget),
    _spec("equity-research-assembly", "equity_research_assembly_assurance", evaluate_equity_research_assembly),
    _spec("finance-evaluation-campaign", "finance_evaluation_campaign_assurance", evaluate_finance_evaluation_campaign),
    _spec("finance-skill-chain", "finance_skill_chain_assurance", evaluate_finance_skill_chain),
    _spec("financial-ui-projection", "financial_ui_projection_assurance", evaluate_financial_ui_projection),
    _spec("financial-workspace", "financial_workspace_assurance", evaluate_financial_workspace),
    _spec("forecast-experiment-comparison", "forecast_experiment_comparison_assurance", evaluate_forecast_experiment_comparison),
    _spec("formal-harness-trace", "formal_harness_trace_assurance", evaluate_formal_harness_trace),
    _spec("harness-benchmark", "harness_benchmark_assurance", evaluate_harness_benchmark),
    _spec("household-debt-stress", "household_debt_stress_assurance", evaluate_household_debt_stress),
    _spec("investment-research-pipeline", "investment_research_pipeline_assurance", evaluate_investment_research_pipeline),
    _spec("ledger-ingestion-plan", "ledger_ingestion_plan_assurance", evaluate_ledger_ingestion_plan),
    _spec("mcp-runtime-profile", "mcp_runtime_profile_assurance", evaluate_mcp_runtime_profile),
    _spec("multi-stage-contest", "multi_stage_contest_assurance", evaluate_multi_stage_contest),
    _spec("multi-venue-trade-plan", "multi_venue_trade_plan_assurance", evaluate_multi_venue_trade_plan),
    _spec("publication-profile", "publication_profile_assurance", evaluate_publication_profile),
    _spec("terminal-command-admission", "terminal_command_admission_assurance", evaluate_terminal_command_admission),
    _spec("wallet-action", "wallet_action_assurance", evaluate_wallet_action),
)

_REGISTRY = {spec.slug: spec for spec in _SPECS}
if len(_REGISTRY) != len(_SPECS):  # pragma: no cover - import-time invariant
    raise RuntimeError("duplicate convergence assurance slug")


def list_convergence_assurances() -> tuple[dict[str, object], ...]:
    """Return immutable public metadata for every recovered assurance capability."""
    return tuple(spec.public_dict() for spec in _SPECS)


def evaluate_convergence_assurance(slug: str, evidence: Any) -> AssuranceReport:
    """Evaluate one named assurance in the closed-world, non-authoritative registry."""
    try:
        spec = _REGISTRY[slug]
    except KeyError as exc:
        raise KeyError(f"unknown convergence assurance: {slug}") from exc
    report = spec.evaluator(evidence)
    if report.mutation_performed or report.authority_granted:  # defense in depth
        raise RuntimeError("assurance evaluator violated non-authoritative contract")
    return report


__all__ = [
    "ConvergenceAssuranceSpec",
    "evaluate_convergence_assurance",
    "list_convergence_assurances",
]
