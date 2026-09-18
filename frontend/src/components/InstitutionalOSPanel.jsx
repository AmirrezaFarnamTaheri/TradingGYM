import { useEffect, useMemo, useRef, useState } from 'react';
import { institutionalAPI } from '../api';
import { useToast } from './Toast';

const DOMAINS = [
  ['source-mining/evaluate', 'Source archaeology', 'Hash-backed donor and historical semantic saturation.'],
  ['market-intelligence/regime', 'Market regimes', 'Trend, volatility, liquidity, and strategy-suitability evidence.'],
  ['alpha-factory/evaluate', 'Alpha factory', 'Falsifiable hypotheses, replication, challenge evidence, and peer-review gates.'],
  ['signal-graph/analyze', 'Signal graph', 'Indicator, factor, strategy, regime, experiment, and risk relationships.'],
  ['portfolio/construct', 'Portfolio construction', 'Equal weight, inverse volatility, minimum variance, risk parity, and diversification.'],
  ['portfolio/digital-twin', 'Portfolio digital twin', 'FX-aware scenario equity, liquidation value, and exposure projections.'],
  ['execution/microstructure', 'Market microstructure', 'Bounded order-book consumption, queue position, impact, and partial fills.'],
  ['execution/routing', 'Smart routing review', 'Cost, reliability, capacity, fill probability, and counterparty-aware route proposals.'],
  ['execution/quality', 'Execution quality', 'Arrival, VWAP, TWAP, delay, adverse-selection, fee, and venue attribution.'],
  ['agents/workforce', 'Agent workforce', 'Role and permission intersection with dependency-safe task planning.'],
  ['agents/memory/query', 'Agent memory', 'Provenance, expiry, supersession, confidence, and failure-lesson retrieval.'],
  ['models/lifecycle', 'Model lifecycle', 'Closed research-to-retirement transition assurance.'],
  ['models/explain', 'Explainability', 'Contributions, counterfactuals, uncertainty, and confidence evidence.'],
  ['research/causal', 'Causal research', 'DAG validation and assumption-explicit synthetic-control evidence.'],
  ['data-platform/evaluate', 'Data platform', 'Lake partitions, vendor agreement, quality, lineage, and point-in-time features.'],
  ['risk/enterprise', 'Enterprise risk', 'Market, credit, liquidity, operational, model, and compliance aggregation.'],
  ['governance/policy-compliance', 'Policy and compliance', 'Policy-as-code, RFC completeness, and framework control evidence.'],
  ['distributed/assure', 'Distributed assurance', 'Fenced work planning, event replay, and dry-run chaos readiness.'],
  ['product/evaluate', 'Product and collaboration', 'Terminal freshness, marketplace admission, and review readiness.'],
  ['scientific-lab/evaluate', 'Scientific lab', 'Reproduction artefacts, benchmarks, and non-authoritative strategy genomes.'],
  ['assurance/complete-convergence', 'Complete convergence', 'Fresh, full-payload qualification across all twenty domains.'],
  ['absorption/behavioural-benchmarks', 'Donor contract benchmarks', 'Per-donor static-contract and differential-runtime evidence with explicit allowed divergences.'],
  ['absorption/feature-store', 'Feature-store parity', 'Offline/online equivalence, freshness, ownership, and point-in-time correctness.'],
  ['absorption/market-replay', 'Market replay', 'Sequence-, chronology-, and hash-chain-verified deterministic event reconstruction.'],
  ['absorption/financing-inventory', 'Borrow and funding', 'Borrow availability, recalls, inventory limits, funding drag, and cost ceilings.'],
  ['absorption/performance-attribution', 'P&L attribution', 'Exact reconciliation of signal, execution, fee, funding, FX, and allocation components.'],
  ['absorption/knowledge-impact', 'Knowledge impact', 'Temporal evidence graph traversal for datasets, models, controls, incidents, and releases.'],
  ['absorption/workflow-assurance', 'Durable workflow assurance', 'Dependency, idempotency, approval, compensation, and cycle review without execution.'],
  ['absorption/dynamic-mutation-coverage', 'Runtime and mutation coverage', 'Executed surfaces, negative/recovery paths, and invariant-sensitive mutation evidence.'],
  ['absorption/saturation', 'Accessible-source saturation', 'Final surface, symbol, workflow, benchmark, authority, and residual-value closure.'],
];

const SAMPLE = {
  'market-intelligence/regime': {
    market_id: 'BTC-USDT',
    returns: Array(60).fill(0.001),
    spread_bps: Array(60).fill(5),
    volume: Array(60).fill(100),
  },
  'portfolio/construct': {
    assets: [
      { asset_id: 'BTC', expected_return: 0.1, volatility: 0.5, minimum_weight: 0, maximum_weight: 0.8 },
      { asset_id: 'ETH', expected_return: 0.08, volatility: 0.4, minimum_weight: 0, maximum_weight: 0.8 },
    ],
    correlation_matrix: [[1, 0.6], [0.6, 1]],
    method: 'risk-parity',
  },
};

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === 'object') {
    return Object.keys(value).sort().reduce((output, key) => {
      output[key] = canonicalize(value[key]);
      return output;
    }, {});
  }
  return value;
}

async function sha256Json(value) {
  const canonical = JSON.stringify(canonicalize(value));
  const bytes = new TextEncoder().encode(canonical);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, '0')).join('');
}

function newIdempotencyKey() {
  if (typeof crypto.randomUUID === 'function') return `institutional-${crypto.randomUUID()}`;
  return `institutional-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function DomainCard({ path, title, description, active, onSelect, disabled }) {
  return (
    <button
      type="button"
      className={`status-card ${active ? 'active' : ''}`}
      onClick={() => onSelect(path)}
      disabled={disabled}
      style={{ textAlign: 'left', cursor: disabled ? 'not-allowed' : 'pointer', minHeight: 145 }}
      aria-pressed={active}
    >
      <div className="status-label">{title}</div>
      <p>{description}</p>
      <code style={{ fontSize: 11 }}>/institutional/{path}</code>
    </button>
  );
}

export default function InstitutionalOSPanel() {
  const toast = useToast();
  const [selected, setSelected] = useState('market-intelligence/regime');
  const [payload, setPayload] = useState(JSON.stringify(SAMPLE['market-intelligence/regime'], null, 2));
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [artifacts, setArtifacts] = useState([]);
  const [artifactId, setArtifactId] = useState('market-intelligence-review');
  const [actor, setActor] = useState('gui-operator');
  // Generation counters: every request stamps the current value and only
  // applies its result if it is still the latest.  A slow response that lands
  // after a newer request (or a domain switch) started is dropped instead of
  // overwriting fresher state.
  const evaluateSeq = useRef(0);
  const artifactsSeq = useRef(0);
  const [loadingArtifacts, setLoadingArtifacts] = useState(false);
  const selectedDomain = useMemo(() => DOMAINS.find(([path]) => path === selected), [selected]);
  const domain = selected.split('/')[0];

  const loadArtifacts = async () => {
    const seq = ++artifactsSeq.current;
    setLoadingArtifacts(true);
    try {
      const response = await institutionalAPI.artifacts(domain, 100);
      if (seq !== artifactsSeq.current) return;
      setArtifacts(response.data);
    } catch (requestError) {
      if (seq !== artifactsSeq.current) return;
      setError(requestError.message || 'Could not load institutional artefacts.');
    } finally {
      if (seq === artifactsSeq.current) setLoadingArtifacts(false);
    }
  };

  useEffect(() => {
    loadArtifacts();
  }, [domain]);

  const selectDomain = (path) => {
    // Invalidate any review or artefact request still in flight for the
    // previous domain: its response belongs to a stale selection.
    evaluateSeq.current += 1;
    artifactsSeq.current += 1;
    setSelected(path);
    setPayload(JSON.stringify(SAMPLE[path] || {}, null, 2));
    setArtifactId(`${path.replaceAll('/', '-')}-review`);
    setResult(null);
    setError('');
  };

  const evaluate = async () => {
    setError('');
    setResult(null);
    let parsed;
    try {
      parsed = JSON.parse(payload);
    } catch {
      setError('Payload is not valid JSON.');
      return;
    }
    setRunning(true);
    const seq = ++evaluateSeq.current;
    try {
      const response = await institutionalAPI.evaluate(selected, parsed);
      // Only the latest request's response may update the panel; an older,
      // slower one is discarded so it can never overwrite fresher state.
      if (seq !== evaluateSeq.current) return;
      setResult(response.data);
      toast.success('Bounded institutional review completed.');
    } catch (requestError) {
      if (seq !== evaluateSeq.current) return;
      setError(requestError.message || 'Institutional review failed.');
    } finally {
      if (seq === evaluateSeq.current) setRunning(false);
    }
  };

  const saveArtifact = async () => {
    if (!result) {
      setError('Run a bounded review before saving an artefact.');
      return;
    }
    if (!artifactId.trim() || !actor.trim()) {
      setError('Artifact ID and actor are required.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const existing = artifacts.find((item) => item.artifact_id === artifactId.trim());
      const reportSha256 = await sha256Json(result);
      await institutionalAPI.saveArtifact({
        domain,
        report_sha256: reportSha256,
        report: result,
        observed_at: result.observed_at || new Date().toISOString(),
        idempotency_key: newIdempotencyKey(),
        artifact_id: artifactId.trim(),
        expected_revision: existing?.revision || 0,
        actor: actor.trim(),
        evidence_ids: [],
      });
      await loadArtifacts();
      toast.success('Institutional review saved with an immutable revision and audit receipt.');
    } catch (requestError) {
      setError(requestError.message || 'Could not save institutional artefact.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="info-box" style={{ marginBottom: 16 }}>
        <strong>Non-authoritative institutional operating system</strong>
        <p>
          Every plane, including final donor-absorption assurance, produces review evidence only. No endpoint in this workspace can submit an order,
          move funds, promote a model, deploy a change, install a package, or inject a chaos fault.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(245px, 1fr))', gap: 12 }}>
        {DOMAINS.map(([path, title, description]) => (
          <DomainCard
            key={path}
            path={path}
            title={title}
            description={description}
            active={selected === path}
            onSelect={selectDomain}
            disabled={running}
          />
        ))}
      </div>

      <div className="section" style={{ marginTop: 18 }}>
        <h3>{selectedDomain?.[1] || 'Institutional evaluator'}</h3>
        <p>{selectedDomain?.[2]}</p>
        <label>
          <span className="status-label">Request JSON</span>
          <textarea
            value={payload}
            onChange={(event) => setPayload(event.target.value)}
            spellCheck={false}
            rows={18}
            style={{ width: '100%', fontFamily: 'monospace', padding: 12 }}
          />
        </label>
        <button type="button" className="btn btn-primary" onClick={evaluate} disabled={running}>
          {running ? 'Evaluating…' : 'Run bounded review'}
        </button>
        {error && <div className="error-message" role="alert" style={{ marginTop: 12 }}>{error}</div>}
        {result && (
          <div style={{ marginTop: 14 }}>
            <div className="status-label">Review artefact</div>
            <pre style={{ maxHeight: 520, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(result, null, 2)}
            </pre>
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr auto', gap: 10, alignItems: 'end' }}>
              <label>
                <span className="status-label">Durable artifact ID</span>
                <input value={artifactId} onChange={(event) => setArtifactId(event.target.value)} />
              </label>
              <label>
                <span className="status-label">Actor</span>
                <input value={actor} onChange={(event) => setActor(event.target.value)} />
              </label>
              <button type="button" className="btn btn-primary" onClick={saveArtifact} disabled={saving}>
                {saving ? 'Saving…' : 'Save reviewed evidence'}
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="section" style={{ marginTop: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h3>Durable review history</h3>
            <p>CAS-versioned, hash-chained reports for the selected domain. These records remain non-authoritative.</p>
          </div>
          <button
            type="button"
            className="btn"
            onClick={loadArtifacts}
            disabled={loadingArtifacts}
            aria-busy={loadingArtifacts}
          >
            {loadingArtifacts ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
        {artifacts.length === 0 ? (
          <p>No saved reports for <code>{domain}</code>.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%' }}>
              <thead><tr><th>Artifact</th><th>Revision</th><th>Observed</th><th>Saved</th><th>Actor</th></tr></thead>
              <tbody>
                {artifacts.map((item) => (
                  <tr key={item.artifact_id}>
                    <td><code>{item.artifact_id}</code></td>
                    <td>{item.revision}</td>
                    <td>{item.observed_at}</td>
                    <td>{item.saved_at}</td>
                    <td>{item.actor}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
