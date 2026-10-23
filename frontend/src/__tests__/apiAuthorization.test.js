import { afterEach, describe, expect, it } from 'vitest';
import api, { authzAPI, forgeAPI, playgroundAPI, systemAPI } from '../api';

const originalAdapter = api.defaults.adapter;

afterEach(() => {
  api.defaults.adapter = originalAdapter;
});

function captureRequests() {
  const requests = [];
  api.defaults.adapter = async (config) => {
    requests.push(config);
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  };
  return requests;
}

describe('actor-bound request configuration', () => {
  it('exposes the same authenticated client to default and named API consumers', async () => {
    const clientModule = await import('../api');
    expect(clientModule.api).toBe(api);
  });

  it('preserves actor credentials, payloads, and per-endpoint options', async () => {
    const requests = captureRequests();
    await authzAPI.issue({ principal: 'agent-1' }, 'operator-a', 'key-a');
    await playgroundAPI.run({ strategy: 'bounded' }, 'operator-b', 'key-b');
    await forgeAPI.memoryAdvice(['regime:chop'], 'operator-c', 'key-c');

    requests.forEach((request, index) => {
      const suffix = ['a', 'b', 'c'][index];
      expect(request.headers['X-Actor']).toBe(`operator-${suffix}`);
      expect(request.headers['X-Actor-Key']).toBe(`key-${suffix}`);
    });
    expect(JSON.parse(requests[0].data)).toEqual({ principal: 'agent-1' });
    expect(requests[1].timeout).toBe(120000);
    expect(requests[2].params).toEqual({ tags: ['regime:chop'] });
    expect(requests[2].paramsSerializer.indexes).toBeNull();
  });

  it('does not retain actor credentials in subsequent ordinary requests', async () => {
    const requests = captureRequests();
    await authzAPI.revoke('grant-1', 'operator-a', 'key-a');
    await systemAPI.health();
    expect(requests[1].headers['X-Actor']).toBeUndefined();
    expect(requests[1].headers['X-Actor-Key']).toBeUndefined();
  });
});
