import { act } from 'react-dom/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TrainingPanel from '../TrainingPanel';
import useStore from '../../store';
import { flushPromises, renderWithClient, waitForRendered } from '../../test-utils.jsx';

const { startSpy, stopSpy, statusSpy, closeSpy, socketRef } = vi.hoisted(() => ({
  startSpy: vi.fn(async () => ({ data: { status: 'started' } })),
  stopSpy: vi.fn(async () => ({ data: { status: 'stopped', remaining: 0 } })),
  statusSpy: vi.fn(async () => ({ data: { status: 'idle' } })),
  closeSpy: vi.fn(),
  socketRef: { current: null },
}));

vi.mock('../../api', () => ({
  trainingAPI: {
    start: startSpy,
    stop: stopSpy,
    getStatus: statusSpy,
  },
  createWebSocket: () => {
    const socket = { close: closeSpy };
    socketRef.current = socket;
    return socket;
  },
}));

beforeEach(() => {
  startSpy.mockReset().mockResolvedValue({ data: { status: 'started' } });
  stopSpy.mockClear();
  statusSpy.mockReset().mockResolvedValue({ data: { status: 'idle' } });
  closeSpy.mockClear();
  socketRef.current = null;
  useStore.getState().clearLogs();
});

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
      (button) => button.textContent.includes('START TRAINING') && !button.disabled
    );
    expect(start.disabled).toBe(false);
    start.click();
    await flushPromises();
    expect(startSpy).toHaveBeenCalledWith('training_local.yaml', 'btc-ppo-v2');
    cleanup();
  });
});

it('ignores malformed or invalid WebSocket logs without crashing', async () => {
  const { container, cleanup } = renderWithClient(<TrainingPanel />);
  expect(socketRef.current).not.toBeNull();
  act(() => {
    socketRef.current.onmessage({ data: '{invalid-json' });
    socketRef.current.onmessage({ data: '{"message":"no level"}' });
  });
  expect(container.querySelectorAll('.log-entry')).toHaveLength(0);

  act(() => {
    socketRef.current.onmessage({
      data: JSON.stringify({ level: 'INFO', message: 'hello', source: 'TRAIN' }),
    });
  });
  await flushPromises();
  expect(container.querySelector('.log-message').textContent).toBe('hello');
  cleanup();
});


it('blocks starting while authoritative training status is unknown', async () => {
  statusSpy.mockReturnValueOnce(new Promise(() => {}));
  const { container, cleanup } = renderWithClient(<TrainingPanel />);
  setValue(container.querySelector('#training-model-version'), 'candidate-unknown');
  const start = await waitForRendered(container, 'button', node => node.textContent.includes('START TRAINING'));
  expect(start.disabled).toBe(true);
  expect(container.textContent).toContain('UNKNOWN');
  expect(startSpy).not.toHaveBeenCalled();
  cleanup();
});

it('shows failed status as unavailable and recovers through a read-only retry', async () => {
  statusSpy.mockRejectedValueOnce(new Error('offline'));
  const { container, cleanup } = renderWithClient(<TrainingPanel />);
  setValue(container.querySelector('#training-model-version'), 'candidate-retry');
  const alert = await waitForRendered(container, '[role="alert"]');
  expect(alert.textContent).toContain('Training status unavailable');
  const start = Array.from(container.querySelectorAll('button')).find(node => node.textContent.includes('START TRAINING'));
  expect(start.disabled).toBe(true);
  statusSpy.mockResolvedValueOnce({ data: { status: 'idle' } });
  const retry = await waitForRendered(container, 'button', node => node.textContent.includes('Retry status'));
  await act(async () => retry.click());
  await waitForRendered(container, 'button', node => node.textContent.includes('START TRAINING') && !node.disabled);
  expect(startSpy).not.toHaveBeenCalled();
  cleanup();
});

it.each(['starting', 'stopping'])('does not start a competing job during %s', async (state) => {
  statusSpy.mockResolvedValueOnce({ data: { status: state } });
  const { container, cleanup } = renderWithClient(<TrainingPanel />);
  setValue(container.querySelector('#training-model-version'), 'candidate-busy');
  await waitForRendered(container, 'strong', node => node.textContent === state.toUpperCase());
  const start = Array.from(container.querySelectorAll('button')).find(node => node.textContent.includes('START TRAINING'));
  expect(start.disabled).toBe(true);
  cleanup();
});

it('allows another stop request while worker exit is unconfirmed', async () => {
  statusSpy.mockResolvedValue({ data: { status: 'stopping' } });
  const { container, cleanup } = renderWithClient(<TrainingPanel />);
  const stop = await waitForRendered(container, 'button', node => node.textContent === 'Retry stop request');
  expect(stop.disabled).toBe(false);
  await act(async () => stop.click());
  expect(stopSpy).toHaveBeenCalledTimes(1);
  cleanup();
});

it('rejects malformed process status instead of assuming idle', async () => {
  statusSpy.mockResolvedValueOnce({ data: { status: 'not-a-process-state' } });
  const { container, cleanup } = renderWithClient(<TrainingPanel />);
  await waitForRendered(container, '[role="alert"]');
  expect(container.textContent).toContain('UNKNOWN');
  cleanup();
});

it('holds the start control until the post-mutation status check completes', async () => {
  let resolveStatus;
  statusSpy.mockResolvedValueOnce({ data: { status: 'idle' } })
    .mockReturnValueOnce(new Promise(resolve => { resolveStatus = resolve; }));
  const { container, cleanup } = renderWithClient(<TrainingPanel />);
  setValue(container.querySelector('#training-model-version'), 'candidate-check');
  const start = await waitForRendered(container, 'button', node => node.textContent.includes('START TRAINING') && !node.disabled);
  await act(async () => start.click());
  await waitForRendered(container, 'button', node => node.textContent.includes('Starting') || !node.disabled);
  expect(start.disabled).toBe(true);
  await act(async () => resolveStatus({ data: { status: 'running' } }));
  await waitForRendered(container, 'strong', node => node.textContent === 'RUNNING');
  expect(start.disabled).toBe(true);
  cleanup();
});

it('retains failed-start feedback in the panel', async () => {
  statusSpy.mockResolvedValueOnce({ data: { status: 'idle' } });
  startSpy.mockRejectedValueOnce(new Error('worker unavailable'));
  const { container, cleanup } = renderWithClient(<TrainingPanel />);
  setValue(container.querySelector('#training-model-version'), 'candidate-failure');
  const start = await waitForRendered(container, 'button', node => node.textContent.includes('START TRAINING') && !node.disabled);
  await act(async () => start.click());
  const alert = await waitForRendered(container, '[role="alert"]');
  expect(alert.textContent).toContain('Training could not start');
  cleanup();
});

it('never stores opaque WebSocket metadata or prints labeled credentials', async () => {
  useStore.getState().clearLogs();
  const { container, cleanup } = renderWithClient(<TrainingPanel />);
  act(() => {
    socketRef.current.onmessage({
      data: JSON.stringify({
        level: 'INFO',
        source: 'TRAIN',
        message: 'api_key=unsafe-test-token completed',
        secret: 'another-sensitive-value',
      }),
    });
  });
  await flushPromises();
  const message = await waitForRendered(container, '.log-message');
  expect(message.textContent).toContain('[REDACTED]');
  expect(message.textContent).not.toContain('unsafe-test-token');
  expect(JSON.stringify(useStore.getState().logs)).not.toContain('another-sensitive-value');
  cleanup();
});
