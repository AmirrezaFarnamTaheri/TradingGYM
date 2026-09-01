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

export default function IntelligencePanel() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
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
  }, []);

  return (
    <div className="panel intelligence-panel">
      <h2>🧠 Intelligence</h2>
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
                  </tr>
                ))}
              </tbody>
            </table>
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
