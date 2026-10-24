import { describe, expect, it, beforeEach, vi } from 'vitest';
import { act } from 'react-dom/test-utils';

import TradingPanel from '../TradingPanel';
import useStore from '../../store';
import { renderWithClient, flushPromises, waitForRendered } from '../../test-utils.jsx';

const { toastSpies, startSpy, stopSpy, killSpy, getStatusSpy, mockWebSocket } = vi.hoisted(() => ({
  toastSpies: {
    info: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
  startSpy: vi.fn(() => Promise.resolve({ data: { pid: 123 } })),
  stopSpy: vi.fn(() => Promise.resolve({ data: { status: 'stopped' } })),
  killSpy: vi.fn(() => Promise.resolve({ processes_killed: [] })),
  getStatusSpy: vi.fn(() => Promise.resolve({ data: { status: 'idle' } })),
  mockWebSocket: { close: vi.fn(), connect: vi.fn(), disconnect: vi.fn() },
}));

vi.mock('../../api', () => ({
  tradingAPI: {
    start: startSpy,
    stop: stopSpy,
    getStatus: getStatusSpy,
  },
  emergencyAPI: {
    killAll: killSpy,
  },
  modelsAPI: {
    getActive: vi.fn(() => Promise.resolve({ data: { active_model: 'model-42' } })),
  },
  createReconnectingWebSocket: vi.fn(() => mockWebSocket),
}));

vi.mock('../Toast', () => ({
  useToast: () => toastSpies,
}));

describe('TradingPanel', () => {
  beforeEach(() => {
    useStore.setState({
      metrics: {
        balance: 0,
        pnl: 0,
        total_positions: 0,
        margin_ratio: 'nan',
      },
    });

    Object.values(toastSpies).forEach((fn) => fn.mockClear());
    startSpy.mockClear();
    stopSpy.mockClear();
    killSpy.mockClear();
    getStatusSpy.mockClear();
    mockWebSocket.close.mockClear();
  });

  it('renders safe fallback for invalid margin ratio', async () => {
    getStatusSpy.mockResolvedValue({ data: { status: 'running' } });
    const { container, cleanup } = renderWithClient(<TradingPanel />);
    await flushPromises();

    // 'nan' from the feed must never surface as 'NaN' in the panel.
    const margin = await waitForRendered(
      container,
      '[aria-label="Margin Used: 0.0%"]'
    );
    expect(margin).not.toBeNull();
    expect(margin.textContent).not.toContain('NaN');

    cleanup();
  });

  it('starts paper trading with the governed active model', async () => {
    // The safe-fallback test above flips the shared spy to 'running'; pin
    // this one back to idle so the start controls are rendered.
    getStatusSpy.mockResolvedValue({ data: { status: 'idle' } });
    const { container, cleanup } = renderWithClient(<TradingPanel />);
    // Active model arrives through the authenticated /models/active query.
    await waitForRendered(container, '.info-box', (node) =>
      node.textContent?.includes('model-42')
    );

    const startButton = await waitForRendered(
      container,
      'button',
      (btn) => btn.textContent?.toUpperCase().includes('START PAPER TRADING')
    );

    await act(async () => {
      startButton.click();
      await Promise.resolve();
    });

    await flushPromises();
    await flushPromises();

    // Paper mode carries no authorisation payload; only live mode does.
    expect(startSpy).toHaveBeenCalledWith('paper', 'model-42');
    // Success toast fires from the mutation's onSuccess handler.
    expect(toastSpies.success).toHaveBeenCalledWith('PAPER worker started. Check session status for readiness.');

    cleanup();
  });

  it('does not claim stopped while the backend still has remaining children', async () => {
    getStatusSpy.mockResolvedValue({ data: { status: 'running', mode: 'paper' } });
    stopSpy.mockResolvedValueOnce({ data: { status: 'stopping', remaining: 1 } });
    const { container, cleanup } = renderWithClient(<TradingPanel />);
    const button = await waitForRendered(container, 'button', (node) => node.textContent?.includes('STOP TRADING'));
    await act(async () => { button.click(); await Promise.resolve(); });
    await flushPromises(); await flushPromises();
    expect(toastSpies.info).toHaveBeenCalledWith('Stop requested; worker exit is not yet confirmed');
    cleanup();
  });
});
