import { act } from 'react-dom/test-utils';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FirstRunJourney from '../FirstRunJourney';
import { api, setApiKey, clearApiKey } from '../../api';

const ID = 'a'.repeat(32);
const base = { run_id: ID, state: 'succeeded', artifact_available: true,
  worker_identity_verified: false, audit_delivery_pending: 0, cancellation_requested: false };

describe('first-run workflow through Axios envelopes', () => {
  let container, root, adapter, query, requests, navigate;
  beforeEach(() => {
    setApiKey('fixture-key');
    adapter = api.defaults.adapter;
    requests = [];
    navigate = vi.fn();
    query = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div'); document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    query.clear(); container.remove(); api.defaults.adapter = adapter; clearApiKey();
  });
  const response = (config, data) => ({ status: 200, headers: {}, config, data });
  async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); }); }
  async function mount() {
    await act(async () => root.render(<QueryClientProvider client={query}>
      <FirstRunJourney onNavigate={navigate} onClose={() => {}} />
    </QueryClientProvider>));
    await settle();
  }
  async function click(label) {
    const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === label);
    expect(button, label).toBeTruthy();
    await act(async () => button.click()); await settle();
  }
  it('runs, inspects a completed artifact and navigates without inventing IDs', async () => {
    api.defaults.adapter = async (config) => {
      requests.push(config);
      if (config.url === '/models/active') return response(config, { active_model: null });
      if (config.url === '/runs/example' && config.method === 'get') return response(config, []);
      if (config.url.endsWith('/artifact')) return response(config, { run_id: ID, bars: 96, trade_count: 2,
        data_kind: 'synthetic_fixture', costs_kind: 'modeled_fee_and_spread',
        metrics: { total_return_pct: 1.2, max_drawdown: .03 }, manifest: { window_sha256: 'fixture' } });
      return response(config, base);
    };
    await mount();
    expect(container.textContent).toContain('No active model is selected');
    await click('Run example');
    expect(container.textContent).toContain('Completed result');
    expect(container.textContent).toContain('synthetic_fixture');
    const posted = requests.find((item) => item.method === 'post');
    expect(JSON.parse(posted.data).idempotency_key).toMatch(/^example-/);
    expect(posted.headers['X-API-Key']).toBe('fixture-key');
    await click('Open paper session controls');
    expect(navigate).toHaveBeenCalledWith('trading');
  });
  it('reuses the same key after a lost POST response', async () => {
    const keys = [];
    api.defaults.adapter = async (config) => {
      if (config.method === 'post') {
        keys.push(JSON.parse(config.data).idempotency_key);
        throw new Error('Response lost');
      }
      return response(config, config.url === '/models/active' ? { active_model: null } : []);
    };
    await mount(); await click('Run example'); await click('Check / retry the same run');
    expect(keys).toHaveLength(2); expect(keys[0]).toBe(keys[1]);
    expect(container.querySelector('[role="alert"]').textContent).toContain('Check the saved run');
  });
  it('reopens a durable record and preserves unconfirmed cancellation', async () => {
    const running = { ...base, state: 'running', artifact_available: false, cancellation_requested: false };
    api.defaults.adapter = async (config) => {
      if (config.url === '/models/active') return response(config, { active_model: null });
      if (config.url === '/runs/example') return response(config, [running]);
      if (config.url.endsWith('/cancel')) {
        running.cancellation_requested = true;
        throw { config, response: { status: 409, data: { detail: 'worker exit remains unverified' } } };
      }
      return response(config, running);
    };
    await mount(); await click(`${ID.slice(0, 12)} · running`); await click('Request cancellation');
    expect(container.textContent).toContain('Cancellation requested; completion of the stop is unverified');
    expect(container.textContent).not.toContain('Run cancelled');
  });
  it('keeps a delayed cancellation attached to its original run after selection changes', async () => {
    const otherId = 'b'.repeat(32);
    const first = { ...base, state: 'running', artifact_available: false };
    const second = { ...first, run_id: otherId };
    let completeCancellation;
    api.defaults.adapter = async (config) => {
      if (config.url === '/models/active') return response(config, { active_model: null });
      if (config.url === '/runs/example') return response(config, [first, second]);
      if (config.url.endsWith('/cancel')) return new Promise((resolve) => {
        completeCancellation = () => resolve(response(config, { ...first, state: 'cancelled' }));
      });
      return response(config, config.url.endsWith(otherId) ? second : first);
    };
    await mount();
    await click(`${ID.slice(0, 12)} · running`);
    await click('Request cancellation');
    await click(`${otherId.slice(0, 12)} · running`);
    await act(async () => completeCancellation());
    await settle();
    expect(query.getQueryData(['example-run', ID]).state).toBe('cancelled');
    expect(query.getQueryData(['example-run', otherId])).toMatchObject({ run_id: otherId, state: 'running' });
    expect(container.querySelector('[role="status"]').textContent).toContain(otherId);
    expect(container.querySelector('[role="status"]').textContent).not.toContain('cancelled');
  });
});
