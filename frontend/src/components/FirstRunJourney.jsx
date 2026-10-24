import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { exampleRunsAPI, modelsAPI } from '../api';

const terminal = new Set(['succeeded', 'failed', 'cancelled']);
const metricRows = [
  ['Bars', (value) => value?.bars],
  ['Trades', (value) => value?.trade_count],
  ['Return (%)', (value) => value?.metrics?.total_return_pct],
  ['Maximum drawdown', (value) => value?.metrics?.max_drawdown],
];
const display = (value) => typeof value === 'number' && Number.isFinite(value)
  ? value.toLocaleString(undefined, { maximumFractionDigits: 4 }) : 'Unavailable';

export default function FirstRunJourney({ onNavigate, onClose }) {
  const queries = useQueryClient();
  const [selected, setSelected] = useState(null);
  const [compare, setCompare] = useState(false);
  const key = useRef(null);
  const runs = useQuery({ queryKey: ['example-runs'],
    queryFn: () => exampleRunsAPI.list().then((r) => r.data), retry: false });
  const activeModel = useQuery({ queryKey: ['active-model'],
    queryFn: () => modelsAPI.getActive().then((r) => r.data), retry: false });
  const run = useQuery({ queryKey: ['example-run', selected], enabled: Boolean(selected),
    queryFn: () => exampleRunsAPI.get(selected).then((r) => r.data), retry: false,
    refetchInterval: (query) => terminal.has(query.state.data?.state) ? false : 1000 });
  const current = run.data;
  const artifact = useQuery({ queryKey: ['example-artifact', selected],
    enabled: current?.state === 'succeeded', retry: false,
    queryFn: () => exampleRunsAPI.artifact(selected).then((r) => r.data) });
  const previous = (runs.data || []).find((item) => item.run_id !== selected && item.state === 'succeeded');
  const comparison = useQuery({ queryKey: ['example-artifact', previous?.run_id],
    enabled: compare && Boolean(previous), retry: false,
    queryFn: () => exampleRunsAPI.artifact(previous.run_id).then((r) => r.data) });
  const start = useMutation({
    mutationFn: () => {
      key.current ||= `example-${crypto.randomUUID()}`;
      return exampleRunsAPI.start(key.current).then((r) => r.data);
    }, retry: false,
    onSuccess: (value) => {
      setSelected(value.run_id);
      queries.setQueryData(['example-run', value.run_id], value);
      queries.invalidateQueries({ queryKey: ['example-runs'] });
    },
  });
  const cancel = useMutation({ mutationFn: (runId) => exampleRunsAPI.cancel(runId).then((r) => {
      if (r.data?.run_id !== runId) throw new Error('The cancellation response belongs to a different run. Refresh status.');
      return r.data;
    }), retry: false,
    onSuccess: (value, runId) => {
      queries.setQueryData(['example-run', runId], value);
      queries.invalidateQueries({ queryKey: ['example-runs'] });
    },
    onError: (_error, runId) => queries.invalidateQueries({ queryKey: ['example-run', runId] }),
  });
  const busy = start.isPending || (selected && (!current || !terminal.has(current.state)));
  const cancellationPending = cancel.isPending && cancel.variables === selected;
  const issue = start.error || run.error || (cancel.variables === selected && cancel.error) || artifact.error || (compare && comparison.error);
  const comparisonResult = compare ? comparison.data : undefined;
  return <section className="first-run" aria-labelledby="first-run-title">
    <div className="first-run-heading">
      <h2 id="first-run-title">Start with a reference run</h2>
      <button className="btn" onClick={onClose}>Close checklist</button>
    </div>
    <p>Your session is connected. This example checks the local workflow with synthetic data and modeled costs.</p>
    <label htmlFor="example-data">Data source</label>
    <select id="example-data" defaultValue="fixture"><option value="fixture">Included example · 96 five-minute bars</option></select>
    <p>Results demonstrate the workflow. They do not establish market performance or model readiness.</p>
    <div className="first-run-actions">
      {!current && <button className="btn btn-primary" disabled={busy} onClick={() => start.mutate()}>
        {start.isPending ? 'Starting…' : start.isError ? 'Check / retry the same run' : 'Run example'}
      </button>}
      {current && !terminal.has(current.state) && <button className="btn" disabled={cancellationPending}
        onClick={() => cancel.mutate(selected)}>{cancellationPending ? 'Requesting cancellation…' : 'Request cancellation'}</button>}
      {current && terminal.has(current.state) && <button className="btn" onClick={() => {
        key.current = null; setSelected(null); setCompare(false); start.reset(); cancel.reset();
      }}>Prepare another example</button>}
      <button className="btn" onClick={() => runs.refetch()}>Refresh saved runs</button>
    </div>
    {issue && <p role="alert">{issue.message} Check the saved run before starting another attempt.</p>}
    <div role="status" aria-live="polite" aria-atomic="true">
      {current ? <>
        <p>Run <code>{current.run_id}</code>: <strong>{current.state}</strong>.</p>
        {current.cancellation_requested && current.state !== 'cancelled' && <p>Cancellation requested; completion of the stop is unverified.</p>}
        {!terminal.has(current.state) && !current.worker_identity_verified && <p>Worker identity is not currently verified. The durable record remains pending reconciliation.</p>}
        {current.audit_delivery_pending > 0 && <p>Audit delivery is pending.</p>}
        {current.error && <p>{current.error}</p>}
      </> : <p>Select the included example or reopen a saved run.</p>}
    </div>
    {runs.error && <p role="alert">Saved runs are unavailable: {runs.error.message}</p>}
    {(runs.data || []).length > 0 && <details><summary>Reopen a saved run</summary>
      <ul>{runs.data.slice(0, 10).map((item) => <li key={item.run_id}>
        <button className="btn" onClick={() => { setSelected(item.run_id); setCompare(false); start.reset(); cancel.reset(); }}>
          {item.run_id.slice(0, 12)} · {item.state}
        </button>
      </li>)}</ul>
    </details>}
    {artifact.data && <div>
      <h3>Completed result</h3>
      <p>Artifact admitted by hash. Data: {artifact.data.data_kind}. Costs: {artifact.data.costs_kind}.</p>
      <table><caption>Reference results{comparisonResult ? ' compared with the previous completed example' : ''}</caption>
        <thead><tr><th scope="col">Measure</th><th scope="col">Selected run</th>{comparisonResult && <th scope="col">Previous run</th>}</tr></thead>
        <tbody>{metricRows.map(([label, value]) => <tr key={label}><th scope="row">{label}</th>
          <td>{display(value(artifact.data))}</td>{comparisonResult && <td>{display(value(comparisonResult))}</td>}</tr>)}</tbody>
      </table>
      {previous && <button className="btn" onClick={() => setCompare(true)}>Compare with previous result</button>}
      <details><summary>Inspect result provenance</summary><pre>{JSON.stringify(artifact.data.manifest, null, 2)}</pre></details>
    </div>}
    <h3>Continue with your own research</h3>
    <ol className="first-run-steps">
      <li><button className="btn" onClick={() => onNavigate('data')}>Select or download data</button><span>Inspect data coverage before training.</span></li>
      <li><button className="btn" onClick={() => onNavigate('training')}>Train a candidate</button><span>Use the existing training configuration and registered artifacts.</span></li>
      <li><button className="btn" onClick={() => onNavigate('models')}>Inspect and select a model</button><span>
        {activeModel.data?.active_model ? `Active model: ${activeModel.data.active_model}.` : activeModel.error
          ? 'Model readiness is unavailable.' : 'No active model is selected. Paper launch needs an admitted model.'}</span></li>
      <li><button className="btn" onClick={() => onNavigate('trading')}>Open paper session controls</button><span>
        Paper uses a persistent virtual account. The worker validates the model and market-read configuration before starting.</span></li>
      <li><button className="btn" onClick={() => onNavigate('system')}>Inspect processes and recovery status</button><span>
        Reopening a saved run does not repeat it. Financial recovery requires reconciliation.</span></li>
    </ol>
  </section>;
}
