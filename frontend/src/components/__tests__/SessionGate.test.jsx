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
});
