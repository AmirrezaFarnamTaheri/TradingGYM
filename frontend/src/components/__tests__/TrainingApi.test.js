import { describe, expect, it, vi } from 'vitest';

const { postSpy } = vi.hoisted(() => ({
  postSpy: vi.fn(async () => ({ data: { status: 'started' } })),
}));

vi.mock('axios', () => ({
  default: {
    create: () => ({
      post: postSpy,
      get: vi.fn(),
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    }),
  },
}));

import { trainingAPI } from '../../api';

describe('Training request serialization', () => {
  it('includes the backend-required model registry version', async () => {
    await trainingAPI.start('training_rtx3050.yaml', 'btc-ppo-v2');
    expect(postSpy).toHaveBeenCalledWith('/train/start', {
      config_name: 'training_rtx3050.yaml',
      model_version: 'btc-ppo-v2',
    });
  });
});
