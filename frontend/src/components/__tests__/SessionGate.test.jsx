import { act, Simulate } from 'react-dom/test-utils';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import SessionGate from '../SessionGate';
import { api, clearApiKey, getApiKey } from '../../api';

describe('gateway session HTTP boundary', () => {
  let container, root, adapter;
  beforeEach(() => {
    clearApiKey();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    adapter = api.defaults.adapter;
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    api.defaults.adapter = adapter;
    clearApiKey();
  });
  async function mount() {
    await act(async () => root.render(<QueryClientProvider client={new QueryClient()}>
      <SessionGate><p>Protected workspaces</p></SessionGate>
    </QueryClientProvider>));
  }
  async function submit(value) {
    await act(async () => Simulate.change(container.querySelector('input'), { target: { value } }));
    await act(async () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  }
  it('mounts protected workspaces only after a header-authenticated read, and clears on disconnect', async () => {
    let observed;
    api.defaults.adapter = async (config) => {
      observed = { url: config.url, key: config.headers['X-API-Key'] };
      return { status: 200, data: { processes: {} }, headers: {}, config };
    };
    await mount();
    expect(container.textContent).not.toContain('Protected workspaces');
    await submit('fixture-session-key');
    expect(observed).toEqual({ url: '/status', key: 'fixture-session-key' });
    expect(container.textContent).toContain('Protected workspaces');
    expect(localStorage.getItem('trading_gym_api_key')).toBeNull();
    await act(async () => container.querySelector('button').click());
    expect(getApiKey()).toBeNull();
    expect(container.textContent).not.toContain('Protected workspaces');
  });
  it('rejects a key without exposing protected content or retaining the key', async () => {
    api.defaults.adapter = async (config) => { throw { config, response: { status: 403, data: { detail: 'Rejected' } } }; };
    await mount();
    await submit('invalid-fixture-key');
    expect(container.querySelector('[role="alert"]').textContent).toContain('rejected');
    expect(getApiKey()).toBeNull();
    expect(container.textContent).not.toContain('Protected workspaces');
  });
  it.each([[401, 'Missing X-API-Key header'], [403, 'Invalid API key']])('expires a rejected current session with HTTP %s', async (status, detail) => {
    api.defaults.adapter = async (config) => ({ status: 200, data: {}, headers: {}, config });
    await mount();
    await submit('fixture-current-key');
    api.defaults.adapter = async (config) => { throw { config, response: { status, data: { detail } } }; };
    await act(async () => { await api.get('/status').catch(() => {}); });
    expect(getApiKey()).toBeNull();
    expect(container.textContent).not.toContain('Protected workspaces');
    expect(container.textContent).toContain('no longer authorized');
  });
  it('keeps the gateway session when an independent approval is denied', async () => {
    api.defaults.adapter = async (config) => ({ status: 200, data: {}, headers: {}, config });
    await mount();
    await submit('fixture-current-key');
    api.defaults.adapter = async (config) => { throw { config, response: { status: 403, data: { detail: 'Valid X-Approval-Key required' } } }; };
    await act(async () => { await api.post('/models/activate').catch(() => {}); });
    expect(getApiKey()).toBe('fixture-current-key');
    expect(container.textContent).toContain('Protected workspaces');
  });
  it('ignores a late authentication failure from a previous session', async () => {
    let failOldRequest;
    api.defaults.adapter = async (config) => {
      if (config.url === '/slow') return new Promise((_resolve, reject) => {
        failOldRequest = () => reject({ config, response: { status: 403, data: { detail: 'Invalid API key' } } });
      });
      return { status: 200, data: {}, headers: {}, config };
    };
    await mount();
    await submit('fixture-old-key');
    const oldRequest = api.get('/slow').catch(() => {});
    await act(async () => { await Promise.resolve(); });
    await act(async () => container.querySelector('button').click());
    await submit('fixture-new-key');
    await act(async () => { failOldRequest(); await oldRequest; });
    expect(getApiKey()).toBe('fixture-new-key');
    expect(container.textContent).toContain('Protected workspaces');
  });
});
