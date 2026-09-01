"""Institutional trading-intelligence operating-system planes.

Every evaluator in this package is bounded and non-authoritative. Reports may
support human review but cannot trade, move funds, promote models, deploy code,
or mutate authoritative accounting/governance state.
"""

from .agent_memory import MemoryQuery, MemoryQueryReport, MemoryRecord, query_agent_memory
from .agent_workforce import AgentDefinition, AgentRole, AgentTask, TaskAssignment, WorkforcePlanReport, WorkforcePlanRequest, plan_agent_workforce
from .alpha_factory import AlphaEvidence, AlphaFactoryPolicy, AlphaFactoryReport, AlphaFactoryRequest, AlphaHypothesis, HypothesisState, evaluate_alpha_hypothesis
from .archaeology import ArchaeologyPolicy, ArchaeologySurface, RepositoryArchaeologyReport, scan_repository
from .causal_research import CausalEdge, CausalResearchReport, CausalResearchRequest, DonorSeries, evaluate_causal_research
from .complete_convergence import CompleteConvergencePolicy, CompleteConvergenceReport, CompleteConvergenceRequest, qualify_complete_convergence
from .data_platform import DataDomain, DataPartition, DataPlatformReport, DataPlatformRequest, FeatureDefinition, FeatureMaterialisation, QualityObservation, VendorComparison, evaluate_data_platform
from .distributed_chaos import ChaosExperiment, ChaosFault, DistributedChaosReport, DistributedChaosRequest, EventRecord, FederatedWorkUnit, WorkAssignment, WorkerHealth, WorkerNode, evaluate_distributed_chaos
from .enterprise_risk import EnterpriseRiskPolicy, EnterpriseRiskReport, EnterpriseRiskRequest, RiskDomain, RiskExposure, evaluate_enterprise_risk
from .execution_quality import ExecutionFill, ExecutionQualityReport, ExecutionQualityRequest, ExecutionSide, VenueQuality, analyze_execution_quality
from .explainability import ExplainabilityReport, ExplainabilityRequest, FeatureContribution, explain_prediction
from .market_intelligence import LiquidityRegime, MarketRegimeReport, MarketRegimeRequest, RegimePolicy, TrendRegime, VolatilityRegime, classify_market_regime
from .microstructure import BookLevel, BookSide, MicrostructureFill, MicrostructureReport, MicrostructureRequest, simulate_order_book
from .model_lifecycle import LifecycleEvidence, LifecycleState, LifecycleTransitionReport, LifecycleTransitionRequest, evaluate_model_transition
from .policy_compliance import ComplianceControlMapping, PolicyComplianceReport, PolicyComplianceRequest, PolicyOperator, PolicyRule, PolicyRuleResult, RFCArtifact, evaluate_policy_compliance
from .portfolio_construction import ConstructionMethod, PortfolioAssetInput, PortfolioConstructionReport, PortfolioConstructionRequest, construct_portfolio
from .portfolio_twin import PortfolioTwinReport, PortfolioTwinRequest, TwinPosition, TwinScenario, TwinScenarioResult, project_portfolio_twin
from .product_collaboration import CollaborationReview, MarketplacePackage, ProductCollaborationReport, ProductCollaborationRequest, ReviewState, TerminalWidget, WidgetKind, evaluate_product_collaboration
from .repository import InstitutionalArtifactConflict, InstitutionalArtifactCreate, InstitutionalArtifactRepository
from .routing import RouteSlice, RoutingPolicy, RoutingReport, RoutingRequest, RoutingSide, VenueQuote, recommend_routes
from .scientific_lab import BenchmarkResult, GenomeOperation, ReproductionArtifact, ScientificLabReport, ScientificLabRequest, StrategyGene, evaluate_scientific_lab
from .signal_graph import SignalEdge, SignalGraphReport, SignalGraphRequest, SignalNode, SignalNodeKind, analyze_signal_graph
from .source_mining import HistoricalSurface, SourceMiningReport, SourceMiningRequest, SurfaceDisposition, evaluate_source_mining

from .final_absorption import (
    AttributionComponent, AttributionReport, AttributionRequest,
    BehaviouralBenchmarkCase, BehaviouralBenchmarkReport, BehaviouralBenchmarkRequest,
    CoverageAssuranceReport, CoverageAssuranceRequest, DonorSaturationEvidence,
    FeatureContract, FeatureParityObservation, FeatureStoreReport, FeatureStoreRequest,
    FinancingInventoryReport, FinancingInventoryRequest, FinancingPosition,
    KnowledgeEdge, KnowledgeImpactReport, KnowledgeImpactRequest, KnowledgeNode,
    MarketReplayReport, MarketReplayRequest, MutationCase, ReplayEvent, RuntimeSurface,
    SaturationReport, SaturationRequest, Verdict, WorkflowAssuranceReport,
    WorkflowAssuranceRequest, WorkflowStep, analyze_knowledge_impact,
    assure_workflow, attribute_performance, evaluate_behavioural_benchmarks,
    evaluate_convergence_saturation, evaluate_dynamic_and_mutation_coverage,
    evaluate_feature_store, evaluate_financing_inventory, replay_market_events,
)

# Reconstructed late-wave assurances are exposed through one closed-world,
# advisory-only registry rather than as parallel authority-bearing subsystems.
from .convergence_registry import evaluate_convergence_assurance, list_convergence_assurances
__all__ = [name for name in globals() if not name.startswith("_")]
