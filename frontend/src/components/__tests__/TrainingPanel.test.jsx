import { act } from 'react-dom/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TrainingPanel from '../TrainingPanel';
import { flushPromises, renderWithClient, waitForRendered } from '../../test-utils.jsx';

const { startSpy, statusSpy, closeSpy } = vi.hoisted(() => ({
  startSpy: vi.fn(async () => ({ data: { status: 'started' } })),
  statusSpy: vi.fn(async () => ({ data: { status: 'idle' } })),
  closeSpy: vi.fn(),
}));

vi.mock('../../api', () => ({
  trainingAPI: {
    start: startSpy,
    stop: vi.fn(),
    getStatus: statusSpy,
  },
  createWebSocket: () => ({ close: closeSpy }),
}));

function setValue(element, value) {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      'value'
    ).set;
    setter.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('TrainingPanel model-version submission', () => {
  beforeEach(() => {
    startSpy.mockClear();
    statusSpy.mockClear();
    closeSpy.mockClear();
  });

  it('requires a safe version before enabling training', async () => {
    const { container, cleanup } = renderWithClient(<TrainingPanel />);
    const versionField = container.querySelector('#training-model-version');
    const start = await waitForRendered(
      container,
      'button',
      (button) => button.textContent.includes('START TRAINING')
    );

    expect(versionField).not.toBeNull();
    expect(start.disabled).toBe(true);
    setValue(versionField, '../bad');
    expect(start.disabled).toBe(true);
    expect(startSpy).not.toHaveBeenCalled();
    cleanup();
  });

  it('passes the actual default profile and explicit registry version', async () => {
    const { container, cleanup } = renderWithClient(<TrainingPanel />);
    const versionField = container.querySelector('#training-model-version');
    setValue(versionField, 'btc-ppo-v2');
    const start = await waitForRendered(
      container,
      'button',
      (button) => button.textContent.includes('START TRAINING')
    );
    expect(start.disabled).toBe(false);
    start.click();
    await flushPromises();
    expect(startSpy).toHaveBeenCalledWith('training_local.yaml', 'btc-ppo-v2');
    cleanup();
  });
});
