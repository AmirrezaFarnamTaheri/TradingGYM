import { afterEach, describe, expect, it, vi } from 'vitest';

// Each test imports a fresh client so credentials cannot leak between tests.
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe('gateway credentials remain session-only', () => {
  it('ignores build-time credentials for HTTP and key availability', async () => {
    vi.stubEnv('VITE_API_KEY', 'public-bundle-key-must-not-authenticate');
    vi.resetModules();
    const client = await import('../api');
    let headers;
    client.default.defaults.adapter = async (config) => {
      headers = config.headers;
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
    };
    expect(client.hasApiKey()).toBe(false);
    await client.systemAPI.health();
    expect(headers['X-API-Key']).toBeUndefined();
    client.setApiKey('session-only-key');
    expect(client.hasApiKey()).toBe(true);
    await client.systemAPI.health();
    expect(headers['X-API-Key']).toBe('session-only-key');
    client.clearApiKey();
    expect(client.hasApiKey()).toBe(false);
  });

  it('never authenticates a websocket with a build-time credential', async () => {
    vi.stubEnv('VITE_API_KEY', 'public-bundle-key-must-not-authenticate');
    const sockets = [];
    vi.stubGlobal('WebSocket', class {
      constructor(url, protocols) { sockets.push({ url, protocols }); }
      addEventListener() {}
    });
    vi.resetModules();
    const client = await import('../api');
    client.createWebSocket('logs');
    expect(sockets[0].protocols).toEqual(['trading-gym.v1']);
    client.setApiKey('session-only-key');
    client.createWebSocket('metrics');
    expect(sockets[1].protocols).toHaveLength(2);
    expect(sockets[1].url).not.toContain('session-only-key');
    client.clearApiKey();
    client.createWebSocket('metrics');
    expect(sockets[2].protocols).toEqual(['trading-gym.v1']);
  });
});
