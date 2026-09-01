import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { convergenceAPI } from '../api';
import { useToast } from './Toast';

function CapabilityCard({ title, children }) {
  return (
    <div className="status-card" style={{ minHeight: '145px' }}>
      <div className="status-label">{title}</div>
      <div style={{ marginTop: '10px', lineHeight: 1.5 }}>{children}</div>
    </div>
  );
}

export default function ConvergencePanel() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [proposalSummary, setProposalSummary] = useState('');
  const [proposalOperation, setProposalOperation] = useState('research.refresh');
  const [proposalRisk, setProposalRisk] = useState('medium');
  const [proposalPayload, setProposalPayload] = useState('{}');
  const [proposalActor, setProposalActor] = useState('');
  const [proposalActorKey, setProposalActorKey] = useState('');
  const [decisionProposal, setDecisionProposal] = useState(null);
  const [decisionForm, setDecisionForm] = useState({
    actor: '', actorKey: '', approvalKey: '', decision: 'approve', reason: '',
  });

  const proposalNeedsPrincipal = proposalRisk === 'high' || proposalRisk === 'critical';

  const clearDecisionForm = () => {
    setDecisionProposal(null);
    setDecisionForm({ actor: '', actorKey: '', approvalKey: '', decision: 'approve', reason: '' });
  };

  const capabilities = useQuery({
    queryKey: ['convergence-capabilities'],
    queryFn: () => convergenceAPI.capabilities().then(response => response.data),
  });
  const proposals = useQuery({
    queryKey: ['convergence-proposals'],
    queryFn: () => convergenceAPI.proposals().then(response => response.data),
    refetchInterval: 5000,
  });
  const experiments = useQuery({
    queryKey: ['convergence-experiments'],
    queryFn: () => convergenceAPI.experiments().then(response => response.data),
    refetchInterval: 10000,
  });
  const accounting = useQuery({
    queryKey: ['convergence-accounting-accounts'],
    queryFn: () => convergenceAPI.accountingAccounts().then(response => response.data),
    refetchInterval: 15000,
  });
  const drift = useQuery({
    queryKey: ['convergence-drift-baselines'],
    queryFn: () => convergenceAPI.driftBaselines().then(response => response.data),
    refetchInterval: 30000,
  });
  const snapshots = useQuery({
    queryKey: ['convergence-snapshots'],
    queryFn: () => convergenceAPI.snapshots().then(response => response.data),
    refetchInterval: 30000,
  });
  const supplyChain = useQuery({
    queryKey: ['convergence-supply-chain'],
    queryFn: () => convergenceAPI.supplyChain().then(response => response.data),
    staleTime: 60000,
  });
  const datasets = useQuery({
    queryKey: ['convergence-datasets'],
    queryFn: () => convergenceAPI.datasets().then(response => response.data),
    refetchInterval: 15000,
  });
  const triggers = useQuery({
    queryKey: ['convergence-triggers'],
    queryFn: () => convergenceAPI.triggers().then(response => response.data),
    refetchInterval: 10000,
  });
  const audit = useQuery({
    queryKey: ['convergence-audit'],
    queryFn: () => convergenceAPI.verifyAudit().then(response => response.data),
    refetchInterval: 10000,
  });
  const templates = useQuery({
    queryKey: ['convergence-templates'],
    queryFn: () => convergenceAPI.templates().then(response => response.data),
  });
  const identityHealth = useQuery({
    queryKey: ['convergence-identity-health'],
    queryFn: () => convergenceAPI.identityHealth().then(response => response.data),
    refetchInterval: 30000,
  });
  const deploymentHealth = useQuery({
    queryKey: ['convergence-deployment-health'],
    queryFn: () => convergenceAPI.deploymentHealth().then(response => response.data),
    refetchInterval: 30000,
  });

  const pendingCount = useMemo(
    () => (proposals.data || []).filter(item => item.state === 'pending').length,
    [proposals.data]
  );
  const runningExperiments = useMemo(
    () => (experiments.data || []).filter(item => item.state === 'running').length,
    [experiments.data]
  );
  const activeTriggers = useMemo(
    () => (triggers.data || []).filter(item => item.state === 'active').length,
    [triggers.data]
  );

  const createProposal = useMutation({
    mutationFn: () => {
      let payload;
      try {
        payload = JSON.parse(proposalPayload || '{}');
      } catch {
        throw new Error('Proposal payload must be valid JSON');
      }
      if (payload === null || Array.isArray(payload) || typeof payload !== 'object') {
        throw new Error('Proposal payload must be a JSON object');
      }
      return convergenceAPI.createProposal({
        operation: proposalOperation,
        summary: proposalSummary,
        payload,
        risk_tier: proposalRisk,
        expires_in_seconds: 900,
        idempotency_key: `gui:${proposalOperation}:${Date.now()}`,
      }, proposalActor || 'gui-operator', proposalActorKey);
    },
    onSuccess: () => {
      setProposalSummary('');
      setProposalPayload('{}');
      setProposalActorKey('');
      queryClient.invalidateQueries({ queryKey: ['convergence-proposals'] });
      queryClient.invalidateQueries({ queryKey: ['convergence-audit'] });
      toast.success('Proposal created for human review');
    },
    onError: error => toast.error(`Proposal failed: ${error.message}`),
  });

  const decideProposal = useMutation({
    mutationFn: () => convergenceAPI.decideProposal(
      decisionProposal.proposal_id,
      {
        expected_revision: decisionProposal.revision,
        actor: decisionForm.actor,
        decision: decisionForm.decision,
        reason: decisionForm.reason,
      },
      decisionForm.decision === 'approve' ? decisionForm.approvalKey : '',
      decisionForm.actorKey,
    ),
    onSuccess: () => {
      clearDecisionForm();
      queryClient.invalidateQueries({ queryKey: ['convergence-proposals'] });
      queryClient.invalidateQueries({ queryKey: ['convergence-audit'] });
      toast.success('Proposal decision recorded');
    },
    onError: error => toast.error(`Decision failed: ${error.message}`),
  });

  if (capabilities.isLoading) {
    return <div className="panel"><div className="loading">Loading converged capabilities...</div></div>;
  }
  if (capabilities.error) {
    return <div className="panel"><div className="error">Failed to load capabilities: {capabilities.error.message}</div></div>;
  }

  const data = capabilities.data || {};
  const auditLabel = audit.data?.sealed
    ? 'HMAC sealed'
    : audit.data?.valid
      ? 'Chain valid, unsealed'
      : 'Unavailable or invalid';

  return (
    <div className="panel convergence-panel">
      <h2>Converged Trading Intelligence & Governance</h2>
      <p style={{ marginBottom: '20px', color: '#5f6b76' }}>
        Strategy, research, market-data, accounting, experiment, assurance, provider, and agent planes remain isolated
        from live execution. Automation creates bounded jobs, evidence reports, or reviewable proposals and cannot
        bypass the existing execution gateway.
      </p>

      <div className="section">
        <h3>Capability and Authority Boundaries</h3>
        <div className="status-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '15px' }}>
          <CapabilityCard title="Strategy Lab">
            Declarative only: <strong>{data.strategy_lab?.declarative_only ? 'Yes' : 'No'}</strong><br />
            Templates: {data.strategy_lab?.templates?.length || 0}<br />
            Workload and overfitting gates enabled.
          </CapabilityCard>
          <CapabilityCard title="Market & Data">
            Canonical instruments: <strong>{data.market?.canonical_instruments ? 'Enabled' : 'Disabled'}</strong><br />
            Catalogued datasets: {datasets.data?.datasets?.length || 0}<br />
            Failover and quarantine contracts enabled.
          </CapabilityCard>
          <CapabilityCard title="Research">
            Evidence-first: <strong>{data.research?.evidence_first ? 'Yes' : 'No'}</strong><br />
            Registered sources: {data.research?.source_count || 0}<br />
            Coverage, freshness, and citation checks enabled.
          </CapabilityCard>
          <CapabilityCard title="Experiments">
            Total jobs: {(experiments.data || []).length}<br />
            Running leases: <strong>{runningExperiments}</strong><br />
            Heartbeats and stale-worker recovery enabled.
          </CapabilityCard>
          <CapabilityCard title="Risk">
            Position sizing: <strong>{data.risk?.position_sizing ? 'Enabled' : 'Disabled'}</strong><br />
            Portfolio gates: <strong>{data.risk?.portfolio_gates ? 'Enabled' : 'Disabled'}</strong><br />
            Existing execution controls remain authoritative.
          </CapabilityCard>
          <CapabilityCard title="Governance">
            Pending proposals: <strong>{pendingCount}</strong><br />
            Active triggers: {activeTriggers}<br />
            Audit: <strong>{auditLabel}</strong><br />
            Principal registry: <strong>{identityHealth.data?.valid ? 'Ready' : 'Blocked or unavailable'}</strong><br />
            Four-eyes possible: <strong>{identityHealth.data?.separation_of_duties_possible ? 'Yes' : 'No'}</strong>
          </CapabilityCard>
          <CapabilityCard title="Agent Tools">
            Curated tools: {data.agent?.tools?.length || 0}<br />
            Direct live-trade tool: <strong>{data.agent?.direct_live_trade_tool ? 'Enabled' : 'Disabled'}</strong><br />
            Arbitrary-code tool: <strong>{data.agent?.arbitrary_code_tool ? 'Enabled' : 'Disabled'}</strong>
          </CapabilityCard>
          <CapabilityCard title="Promotion Workflow">
            Strategy-to-backtest planning: <strong>{data.workflows?.strategy_to_backtest_plan ? 'Enabled' : 'Disabled'}</strong><br />
            Execution authority granted: <strong>{data.workflows?.execution_authority_granted ? 'Yes' : 'No'}</strong><br />
            Paper evidence and human approval remain mandatory.
          </CapabilityCard>
          <CapabilityCard title="Accounting & Reconciliation">
            Durable accounts: <strong>{(accounting.data || []).length}</strong><br />
            Event replay verification: <strong>{data.accounting?.materialized_state_replay_verified ? 'Enabled' : 'Disabled'}</strong><br />
            Settlement projection: <strong>{data.accounting?.calendar_aware_settlement_projection ? 'Enabled' : 'Disabled'}</strong><br />
            Corporate actions: <strong>{data.accounting?.corporate_action_planning ? 'Plan-only' : 'Unavailable'}</strong>
          </CapabilityCard>
          <CapabilityCard title="Reproducibility & Drift">
            Run manifests: <strong>{data.experiments?.immutable_run_manifests ? 'Enabled' : 'Disabled'}</strong><br />
            Drift baselines: {(drift.data || []).length}<br />
            Temporal leakage audit: <strong>{data.experiments?.point_in_time_leakage_audit ? 'Required' : 'Unavailable'}</strong><br />
            Seeds, source, dependencies, and artifacts are fingerprinted.
          </CapabilityCard>
          <CapabilityCard title="Resilience & Supply Chain">
            Verified snapshots: {(snapshots.data || []).length}<br />
            Dependency policy: <strong>{supplyChain.data?.valid ? 'Valid' : 'Review required'}</strong><br />
            Clock health: <strong>{data.operations?.multi_source_clock_health ? 'Evidence required' : 'Unavailable'}</strong><br />
            Restore remains plan-only through this surface.
          </CapabilityCard>
          <CapabilityCard title="Identity & Duties">
            Configured principals: <strong>{identityHealth.data?.actor_count ?? 0}</strong><br />
            Proposers: {identityHealth.data?.role_counts?.proposer ?? 0} · Approvers: {identityHealth.data?.role_counts?.approver ?? 0} · Executors: {identityHealth.data?.role_counts?.executor ?? 0}<br />
            Secrets exposed by report: <strong>{identityHealth.data?.secrets_exposed ? 'Yes' : 'No'}</strong><br />
            Authority granted by report: <strong>{identityHealth.data?.authority_granted ? 'Yes' : 'No'}</strong>
          </CapabilityCard>
          <CapabilityCard title="Deployment Isolation">
            Mode: <strong>{deploymentHealth.data?.deployment_mode || 'Unavailable'}</strong><br />
            Tenant: <strong>{deploymentHealth.data?.tenant_id || 'Unavailable'}</strong><br />
            Single-tenant enforced: <strong>{deploymentHealth.data?.single_tenant_enforced ? 'Yes' : 'No'}</strong><br />
            Multi-tenant supported: <strong>{deploymentHealth.data?.multi_tenant_supported ? 'Yes' : 'No'}</strong>
          </CapabilityCard>
          <CapabilityCard title="Lifecycle, Rotation & Recovery">
            Retention/legal-hold planning: <strong>{data.governance?.data_retention_and_legal_hold_planning ? 'Enabled' : 'Disabled'}</strong><br />
            Credential rotation readiness: <strong>{data.governance?.credential_rotation_readiness ? 'Enabled' : 'Disabled'}</strong><br />
            RPO/RTO evidence: <strong>{data.operations?.rpo_rto_evidence ? 'Required' : 'Unavailable'}</strong><br />
            Delete, rotate, restore, and failover authority: <strong>Not granted</strong>
          </CapabilityCard>
          <CapabilityCard title="Temporal & Market Integrity">
            Session calendar: <strong>{data.market?.timezone_safe_session_calendar ? 'Enabled' : 'Disabled'}</strong><br />
            Holidays/early closes: <strong>{data.market?.holiday_early_close_and_maintenance_gates ? 'Gated' : 'Ungated'}</strong><br />
            Point-in-time evidence: <strong>{data.experiments?.point_in_time_leakage_audit ? 'Required' : 'Missing'}</strong><br />
            Clock evaluation never mutates the host clock.
          </CapabilityCard>
          <CapabilityCard title="Schema & Data Contracts">
            Compatibility analysis: <strong>{data.schema_assurance?.versioned_contract_compatibility ? 'Enabled' : 'Disabled'}</strong><br />
            Primary-key, event-time, enum, semantic, and classification changes are reviewed.<br />
            Registration or migration authority: <strong>Not granted</strong>
          </CapabilityCard>
          <CapabilityCard title="Treasury & Factor Risk">
            Liquidity ladder: <strong>{data.treasury?.multi_currency_liquidity_ladder ? 'Enabled' : 'Disabled'}</strong><br />
            Factor decomposition: <strong>{data.factor_risk?.psd_covariance_and_risk_contributions ? 'Enabled' : 'Disabled'}</strong><br />
            Cash transfer, hedge, and execution authority: <strong>Not granted</strong>
          </CapabilityCard>
          <CapabilityCard title="Model & Control Assurance">
            Seed/regime challenge tests: <strong>{data.model_risk?.seed_and_regime_challenge_testing ? 'Enabled' : 'Disabled'}</strong><br />
            Independent control evidence: <strong>{data.control_effectiveness?.independent_current_evidence ? 'Required' : 'Unavailable'}</strong><br />
            Promotion and remediation authority: <strong>Not granted</strong>
          </CapabilityCard>
          <CapabilityCard title="Forensics & Change Readiness">
            Chain of custody: <strong>{data.forensics?.chain_of_custody_and_timeline_readiness ? 'Enabled' : 'Disabled'}</strong><br />
            Blast radius: <strong>{data.dependency_impact?.transitive_blast_radius_and_headroom ? 'Evaluated' : 'Unavailable'}</strong><br />
            Rehearsed runbooks: <strong>{data.runbooks?.rehearsal_rollback_and_role_separation ? 'Required' : 'Unavailable'}</strong>
          </CapabilityCard>
          <CapabilityCard title="Cross-plane Assurance">
            Qualification gate: <strong>{data.assurance?.cross_plane_qualification ? 'Enabled' : 'Disabled'}</strong><br />
            Human review only: <strong>{data.assurance?.human_review_only ? 'Yes' : 'No'}</strong><br />
            Promotion authority: <strong>{data.assurance?.promotion_authority_granted ? 'Granted' : 'Not granted'}</strong>
          </CapabilityCard>
        </div>
      </div>

      <div className="section">
        <h3>Strategy Templates</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '12px' }}>
          {Object.entries(templates.data || {}).map(([key, strategy]) => (
            <div className="info-box" key={key}>
              <strong>{strategy.name}</strong>
              <p>{strategy.description || 'Curated declarative template.'}</p>
              <small>{strategy.timeframe} · max position {(strategy.risk.max_position_fraction * 100).toFixed(0)}%</small>
            </div>
          ))}
        </div>
      </div>

      <div className="section">
        <h3>Create Review Proposal</h3>
        <div className="info-box">
          High and critical proposals require a configured proposer principal. Critical operations such as <code>model.activate</code> and <code>trading.start_live</code> must include the exact target values in the JSON payload and later be approved by a different approver principal.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '10px', alignItems: 'end' }}>
          <label>
            <span className="status-label">Operation</span>
            <input
              value={proposalOperation}
              onChange={event => setProposalOperation(event.target.value)}
              maxLength={80}
              style={{ width: '100%', padding: '10px' }}
            />
          </label>
          <label>
            <span className="status-label">Risk tier</span>
            <select
              value={proposalRisk}
              onChange={event => setProposalRisk(event.target.value)}
              style={{ width: '100%', padding: '10px' }}
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          </label>
          <label>
            <span className="status-label">Why this action is requested</span>
            <input
              value={proposalSummary}
              onChange={event => setProposalSummary(event.target.value)}
              maxLength={1000}
              placeholder="Concrete purpose, scope, and expected outcome"
              style={{ width: '100%', padding: '10px' }}
            />
          </label>
          <label style={{ gridColumn: '1 / -1' }}>
            <span className="status-label">Payload JSON</span>
            <textarea
              value={proposalPayload}
              onChange={event => setProposalPayload(event.target.value)}
              rows="4"
              maxLength={131072}
              spellCheck="false"
              style={{ width: '100%', padding: '10px', fontFamily: 'monospace' }}
            />
          </label>
          <label>
            <span className="status-label">Proposer principal {proposalNeedsPrincipal ? '(required)' : '(optional)'}</span>
            <input
              value={proposalActor}
              onChange={event => setProposalActor(event.target.value)}
              autoComplete="username"
              style={{ width: '100%', padding: '10px' }}
            />
          </label>
          <label>
            <span className="status-label">Proposer key {proposalNeedsPrincipal ? '(required)' : '(optional)'}</span>
            <input
              type="password"
              value={proposalActorKey}
              onChange={event => setProposalActorKey(event.target.value)}
              autoComplete="off"
              style={{ width: '100%', padding: '10px' }}
            />
          </label>
          <button
            className="btn btn-primary"
            disabled={
              !proposalSummary.trim()
              || !proposalOperation.trim()
              || createProposal.isPending
              || (proposalNeedsPrincipal && (!proposalActor.trim() || !proposalActorKey))
            }
            onClick={() => createProposal.mutate()}
          >
            {createProposal.isPending ? 'Creating...' : 'Create proposal'}
          </button>
        </div>
      </div>

      <div className="section">
        <h3>Recent Proposals</h3>
        {(proposals.data || []).length === 0 ? (
          <div className="info-box">No proposals have been created.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="models-table" aria-label="Governance proposals">
              <thead><tr><th>Operation</th><th>Summary</th><th>Risk</th><th>State</th><th>Revision</th><th>Expires</th><th>Review</th></tr></thead>
              <tbody>
                {(proposals.data || []).slice(0, 20).map(item => (
                  <tr key={item.proposal_id}>
                    <td><code>{item.operation}</code></td>
                    <td>{item.summary}</td>
                    <td>{item.risk_tier}</td>
                    <td><strong>{item.state}</strong></td>
                    <td>{item.revision}</td>
                    <td>{new Date(item.expires_at).toLocaleString()}</td>
                    <td>
                      <button
                        className="btn btn-small btn-secondary"
                        disabled={item.state !== 'pending'}
                        onClick={() => setDecisionProposal(item)}
                      >
                        Decide
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {decisionProposal && (
        <div className="section" role="dialog" aria-modal="true" aria-label={`Decision for proposal ${decisionProposal.proposal_id}`}>
          <h3>Record proposal decision</h3>
          <div className="info-box">
            <strong>{decisionProposal.operation}</strong> · {decisionProposal.risk_tier} · revision {decisionProposal.revision}<br />
            Created by <strong>{decisionProposal.created_by}</strong>. High/critical approval requires a distinct configured approver plus the independent approval factor.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '10px' }}>
            <label>Decision
              <select value={decisionForm.decision} onChange={event => setDecisionForm({ ...decisionForm, decision: event.target.value })}>
                <option value="approve">Approve</option>
                <option value="reject">Reject</option>
                <option value="cancel">Cancel</option>
              </select>
            </label>
            <label>Approver principal
              <input value={decisionForm.actor} onChange={event => setDecisionForm({ ...decisionForm, actor: event.target.value })} autoComplete="username" />
            </label>
            <label>Principal key
              <input type="password" value={decisionForm.actorKey} onChange={event => setDecisionForm({ ...decisionForm, actorKey: event.target.value })} autoComplete="off" />
            </label>
            {decisionForm.decision === 'approve' && (decisionProposal.risk_tier === 'high' || decisionProposal.risk_tier === 'critical') && (
              <label>Approval factor
                <input type="password" value={decisionForm.approvalKey} onChange={event => setDecisionForm({ ...decisionForm, approvalKey: event.target.value })} autoComplete="off" />
              </label>
            )}
            <label style={{ gridColumn: '1 / -1' }}>Reason
              <input value={decisionForm.reason} onChange={event => setDecisionForm({ ...decisionForm, reason: event.target.value })} maxLength={1000} />
            </label>
          </div>
          <div className="action-buttons" style={{ marginTop: '15px' }}>
            <button
              className="btn btn-primary"
              disabled={
                decideProposal.isPending
                || !decisionForm.actor
                || ((decisionProposal.risk_tier === 'high' || decisionProposal.risk_tier === 'critical') && !decisionForm.actorKey)
                || (decisionForm.decision === 'approve' && (decisionProposal.risk_tier === 'high' || decisionProposal.risk_tier === 'critical') && !decisionForm.approvalKey)
              }
              onClick={() => decideProposal.mutate()}
            >
              {decideProposal.isPending ? 'Recording…' : 'Record decision'}
            </button>
            <button className="btn btn-secondary" onClick={clearDecisionForm}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
