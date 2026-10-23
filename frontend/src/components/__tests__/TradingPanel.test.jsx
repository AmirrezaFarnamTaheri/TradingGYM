import { describe, expect, it, beforeEach, vi } from 'vitest';
import { act, Simulate } from 'react-dom/test-utils';

import TradingPanel from '../TradingPanel';
import useStore from '../../store';
import { renderWithClient, flushPromises, waitForRendered } from '../../test-utils.jsx';

const { toastSpies, startSpy, stopSpy, killSpy, panicSpy, getStatusSpy, getRiskSpy, socketHandlers, mockWebSocket } = vi.hoisted(() => ({
  toastSpies: {
    info: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
  startSpy: vi.fn(() => Promise.resolve({ data: { pid: 123 } })),
  stopSpy: vi.fn(() => Promise.resolve({ data: { status: 'stopped' } })),
  killSpy: vi.fn(() => Promise.resolve({ processes_killed: [] })),
  panicSpy: vi.fn(() => Promise.resolve({ data: { status: 'requested' } })),
  getStatusSpy: vi.fn(() => Promise.resolve({ data: { status: 'idle' } })),
  getRiskSpy: vi.fn(),
  socketHandlers: { current: null },
  mockWebSocket: { close: vi.fn(), connect: vi.fn(), disconnect: vi.fn() },
}));

vi.mock('../../api', () => ({
  tradingAPI: {
    start: startSpy,
    stop: stopSpy,
    getStatus: getStatusSpy,
    emergencyKillAll: killSpy,
    panic: panicSpy,
  },
  emergencyAPI: {
    killAll: killSpy,
  },
  modelsAPI: {
    getActive: vi.fn(() => Promise.resolve({ data: { active_model: 'model-42' } })),
  },
  configAPI: { getSection: getRiskSpy },
  createReconnectingWebSocket: vi.fn((_name, handlers) => { socketHandlers.current = handlers; return mockWebSocket; }),
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
    getRiskSpy.mockReset().mockResolvedValue({ data: {
      leverage: { max_leverage: 12, max_short_leverage: 3, liquidation_margin_ratio: .04, financing: { daily_pct: .03 } },
      shorts: { enabled: true, allowed_symbols: ['BTC', 'ETH'] },
      breaker: { max_loss_pct: 7, max_loss_window_minutes: 45, margin_ratio_floor: .08 },
      costs: { taker_fee_bps: 12 },
    } });
    panicSpy.mockClear();
    mockWebSocket.close.mockClear();
  });

  it('shows unavailable rather than inventing zero for missing or invalid telemetry', async () => {
    getStatusSpy.mockResolvedValue({ data: { status: 'running' } });
    const { container, cleanup } = renderWithClient(<TradingPanel />);
    await flushPromises();

    // 'nan' from the feed must never surface as 'NaN' in the panel.
    const margin = await waitForRendered(
      container,
      '[aria-label="Margin ratio: Unavailable"]'
    );
    expect(margin).not.toBeNull();
    expect(margin.textContent).not.toContain('NaN');
    expect(container.querySelector('[aria-label="Balance: Unavailable"]')).not.toBeNull();

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

  it('renders configured risk values rather than static venue assumptions', async () => {
    getStatusSpy.mockResolvedValue({ data: { status: 'idle' } });
    const { container, cleanup } = renderWithClient(<TradingPanel />);
    await waitForRendered(container, 'caption', node => node.textContent === 'Configured risk and cost limits');
    const rows = Object.fromEntries(Array.from(container.querySelectorAll('tbody tr')).map(row => [row.children[0].textContent, row.children[1].textContent]));
    expect(getRiskSpy).toHaveBeenCalledWith('env');
    expect(rows).toMatchObject({ 'Maximum long leverage': '12×', 'Maximum short leverage': '3×', 'Margin floor': '8%', 'Taker fee': '0.12%' });
    expect(container.textContent).not.toContain('Up to 5x leverage');
    cleanup();
  });

  it('blocks a malformed trading status without hiding emergency controls', async () => {
    getStatusSpy.mockResolvedValue({ data: { status: 'not-a-process-state' } });
    const { container, cleanup } = renderWithClient(<TradingPanel />);
    await waitForRendered(container, '[role="alert"]');
    const start = await waitForRendered(container, 'button', node => node.textContent.includes('START PAPER TRADING'));
    expect(start.disabled).toBe(true);
    expect(container.textContent).toContain('STATUS UNAVAILABLE');
    expect(container.textContent).toContain('KILL ALL PROCESSES');
    cleanup();
  });

  it('formats a negative P&L once and does not manufacture omitted fields', async () => {
    getStatusSpy.mockResolvedValue({ data: { status: 'running' } });
    const { container, cleanup } = renderWithClient(<TradingPanel />);
    await waitForRendered(container, '.metrics-grid');
    act(() => socketHandlers.current.onMessage({ balance: 100, pnl: -12.5, total_positions: 1 }));
    const pnl = await waitForRendered(container, '[aria-label="P&L: -$12.50"]');
    expect(pnl.textContent).toBe('-$12.50');
    expect(container.querySelector('[aria-label="Unrealized P&L: Unavailable"]')).not.toBeNull();
    cleanup();
  });

  it('reports unconfirmed emergency flattening instead of claiming completion', async () => {
    getStatusSpy.mockResolvedValue({ data: { status: 'running' } });
    killSpy.mockResolvedValueOnce({ data: { flatten_confirmed: false, panic_status: 'degraded' } });
    const { container, cleanup } = renderWithClient(<TradingPanel />);
    const open = await waitForRendered(container, 'button', node => node.textContent.includes('KILL ALL PROCESSES'));
    act(() => open.click());
    act(() => Simulate.change(container.querySelector('.emergency-confirm input'), { target: { value: 'fixture-emergency-key' } }));
    const confirm = await waitForRendered(container, 'button', node => node.textContent.includes('CONFIRM KILL ALL'));
    await act(async () => confirm.click());
    await flushPromises();
    expect(toastSpies.warning).toHaveBeenCalledWith(expect.stringContaining('Position closure is unconfirmed'));
    expect(toastSpies.warning).toHaveBeenCalledWith(expect.stringContaining('panic signal could not be saved'));
    expect(toastSpies.error).not.toHaveBeenCalledWith(expect.stringContaining('All processes terminated'));
    cleanup();
  });

  it('describes panic as a request requiring reconciliation', async () => {
    getStatusSpy.mockResolvedValue({ data: { status: 'running' } });
    const confirmation = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { container, cleanup } = renderWithClient(<TradingPanel />);
    const panic = await waitForRendered(container, 'button', node => node.textContent === 'Request panic stop');
    await act(async () => panic.click());
    await flushPromises();
    expect(confirmation).toHaveBeenCalledWith(expect.stringContaining('attempt to close positions'));
    expect(panicSpy).toHaveBeenCalledTimes(1);
    expect(toastSpies.error).toHaveBeenCalledWith('Panic request recorded. Check reconciliation for the position outcome.');
    confirmation.mockRestore();
    cleanup();
  });
});
