/**
 * Intelligence Status Panel
 *
 * Shows which "brain" the bot is configured to run on (RL / local LLM /
 * hosted LLM API / fusion / deterministic rules), live availability per
 * backend, and compute info (GPU/CUDA). Read-only, authenticated like every
 * other surface. Data source: GET /api/v1/intelligence/status.
 */

import { useState, useEffect } from 'react';
import { api } from '../api';

const AVAILABILITY_STYLES = {
  true: { dot: '#27ae60', label: 'Available' },
  false: { dot: '#e74c3c', label: 'Unavailable' },
  null: { dot: '#f39c12', label: 'Worker-scoped' },
};

function AvailabilityDot({ available }) {
  const style = AVAILABILITY_STYLES[String(available)] || AVAILABILITY_STYLES.null;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span
        aria-hidden="true"
        style={{
          width: 10,
          height: 10,
          borderRadius: '50%',
          background: style.dot,
          display: 'inline-block',
        }}
      />
      <span className="sr-only" aria-label={`Availability: ${style.label}`} />
      {style.label}
    </span>
  );
}

function ProviderRequestBudgetCell({ budget }) {
  if (!budget) return <span aria-label="No request cap configured">—</span>;
  if (budget.state === 'unavailable') {
    return <strong role="status">Budget unavailable — admission denied</strong>;
  }
  if (budget.state === 'exhausted') {
    return (
      <strong role="status">
        Exhausted ({budget.used} / {budget.ceiling})
      </strong>
    );
  }
  return (
    <span aria-label={`Provider requests this UTC hour: ${budget.used} of ${budget.ceiling}`}>
      {budget.used} / {budget.ceiling} this UTC hour
    </span>
  );
}

export default function IntelligencePanel() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshCount, setRefreshCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const res = await api.get('/intelligence/status');
        if (!cancelled) {
          setStatus(res.data);
          setError('');
        }
      } catch (err) {
        if (!cancelled) setError(err.message || 'failed to load intelligence status');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [refreshCount]);

  return (
    <div className="panel intelligence-panel">
      <h2>🧠 Intelligence</h2>
      <button
        type="button"
        className="btn btn-secondary"
        aria-label="Refresh model-fleet intelligence status snapshot"
        aria-busy={loading}
        disabled={loading}
        onClick={() => setRefreshCount(count => count + 1)}
      >
        Refresh status
      </button>
      {loading && <div className="loading">Loading intelligence status…</div>}
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {!loading && !error && status && (
        <>
          <div className="info-box">
            <p>
              <strong>Configured mode:</strong> {status.mode}
            </p>
            {status.llm_provider_id && (
              <p>
                <strong>LLM provider:</strong> {status.llm_provider_id}
              </p>
            )}
            {status.selected_hint && (
              <p>
                <strong>Auto-selection hint:</strong> {status.selected_hint}
              </p>
            )}
            {status.system1_provider_ids?.length > 0 && (
              <p>
                <strong>Fast-model roster:</strong> {status.system1_provider_ids.join(', ')}
              </p>
            )}
            {typeof status.system1_memory_enabled === 'boolean' && (
              <p>
                <strong>Experience memory:</strong>{' '}
                {status.system1_memory_enabled ? 'Enabled' : 'Disabled'}
              </p>
            )}
            {typeof status.system1_working_memory_enabled === 'boolean' && (
              <p>
                <strong>Working-memory P&amp;L disclosure:</strong>{' '}
                {status.system1_working_memory_enabled ? 'Enabled' : 'Disabled'}
              </p>
            )}
            <p className="muted">{status.note}</p>
          </div>

          <h3>Backends</h3>
          {status.backends.length === 0 ? (
            <p>No backends registered.</p>
          ) : (
            <table className="intelligence-table">
              <thead>
                <tr>
                  <th scope="col">Backend</th>
                  <th scope="col">Status</th>
                  <th scope="col">Device</th>
                  <th scope="col">Request budget</th>
                </tr>
              </thead>
              <tbody>
                {status.backends.map((b) => (
                  <tr key={b.name}>
                    <td>{b.name}</td>
                    <td>
                      <AvailabilityDot available={b.available} />
                    </td>
                    <td>{b.device || '—'}</td>
                    <td><ProviderRequestBudgetCell budget={b.request_budget} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {Array.isArray(status.provider_presets) && status.provider_presets.length > 0 && (
            <section className="provider-presets" aria-labelledby="provider-presets-heading">
              <h3 id="provider-presets-heading">Provider templates</h3>
              <p className="muted">
                Read-only reference templates. None is installed, enabled, or
                authorized to send account context by appearing here.
              </p>
              <table className="intelligence-table">
                <thead>
                  <tr>
                    <th scope="col">Provider family</th>
                    <th scope="col">Protocol</th>
                    <th scope="col">Default state</th>
                  </tr>
                </thead>
                <tbody>
                  {status.provider_presets.map((preset) => (
                    <tr key={preset.preset_id}>
                      <td>{preset.display_name}</td>
                      <td>{preset.wire_protocol}</td>
                      <td>
                        {preset.enabled_by_default
                          ? 'Requires explicit review'
                          : 'Disabled by default'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {status.compute && (
            <div className="info-box" style={{ marginTop: 12 }}>
              <p>
                <strong>Compute:</strong>{' '}
                {status.compute.torch_installed
                  ? `CUDA ${status.compute.cuda_available ? 'available' : 'unavailable'}${
                      status.compute.device_count
                        ? ` (${status.compute.device_count} device(s))`
                        : ''
                    }`
                  : `torch not installed${status.compute.error ? ` (${status.compute.error})` : ''}`}
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
