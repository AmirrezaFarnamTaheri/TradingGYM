import { describe, expect, it, beforeEach, vi } from 'vitest';

import ModelsPanel from '../ModelsPanel';
import useStore from '../../store';
import { renderWithClient, flushPromises, waitForRendered } from '../../test-utils.jsx';

const { modelsSpy, activeSpy } = vi.hoisted(() => ({
  modelsSpy: vi.fn(() => Promise.resolve({ data: { models: [] } })),
  activeSpy: vi.fn(() => Promise.resolve({ data: { active_model: 'model-7' } })),
}));

vi.mock('../../api', () => ({
  modelsAPI: {
    list: modelsSpy,
    getActive: activeSpy,
  },
}));

vi.mock('../Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

describe('ModelsPanel active-model sync', () => {
  beforeEach(() => {
    modelsSpy.mockClear();
    activeSpy.mockClear();
    useStore.setState({ activeModel: null });
  });

  it('writes the polled active model into the store via useEffect', async () => {
    // TanStack Query v5 removed the useQuery onSuccess option, so the store is
    // synchronized in an effect.  The old onSuccess callback was dead code and
    // setActiveModel never ran, leaving the store stale on every refresh.
    const { container, cleanup } = renderWithClient(<ModelsPanel />);
    await flushPromises();

    await waitForRendered(container, '.info-box');

    expect(activeSpy).toHaveBeenCalled();
    expect(useStore.getState().activeModel).toBe('model-7');

    cleanup();
  });

  it('tracks whatever the poll currently reports, not a stale capture', async () => {
    // The store must reflect the value the query actually returns, picked up on
    // each mount -- the dead onSuccess never synchronized it at all.
    const first = renderWithClient(<ModelsPanel />);
    await flushPromises();
    await waitForRendered(first.container, '.info-box');
    expect(useStore.getState().activeModel).toBe('model-7');
    first.cleanup();

    activeSpy.mockResolvedValue({ data: { active_model: 'model-8' } });
    const second = renderWithClient(<ModelsPanel />);
    await flushPromises();
    await waitForRendered(second.container, '.info-box');
    expect(useStore.getState().activeModel).toBe('model-8');
    second.cleanup();
  });
});


describe('ModelsPanel fetch errors', () => {
  beforeEach(() => {
    modelsSpy.mockReset();
    activeSpy.mockReset();
    modelsSpy.mockResolvedValue({ data: { models: [] } });
    activeSpy.mockResolvedValue({ data: { active_model: 'model-7' } });
    useStore.setState({ activeModel: null });
  });

  it('renders an explicit error state with retry, not an empty registry', async () => {
    // A failed list must read as a failure: the empty state below is grey and
    // passive, while the error state is red and carries a retry action.  An
    // empty table used to be indistinguishable from a broken fetch.
    modelsSpy.mockRejectedValueOnce(new Error('registry unavailable'));

    const { container, cleanup } = renderWithClient(<ModelsPanel />);
    await flushPromises();

    const errorBox = await waitForRendered(container, '.error');
    expect(errorBox.textContent).toContain('Failed to load models');
    expect(errorBox.textContent).toContain('registry unavailable');
    expect(container.querySelector('.empty-state')).toBeNull();

    // Retrying re-fetches: the mock only rejects once, so the genuine empty
    // registry appears and the error state is gone.
    const retry = await waitForRendered(
      container,
      'button',
      (btn) => btn.textContent?.trim() === 'Retry'
    );
    retry.click();
    await flushPromises();

    await waitForRendered(container, '.empty-state');
    expect(container.querySelector('.error')).toBeNull();

    cleanup();
  });
});
