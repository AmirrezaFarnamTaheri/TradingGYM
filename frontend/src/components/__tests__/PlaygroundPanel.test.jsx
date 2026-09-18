import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, test, vi, it, beforeEach } from 'vitest';
import { renderWithClient, flushPromises, waitForRendered } from '../../test-utils.jsx';

import PlaygroundPanel from '../PlaygroundPanel';

const { runSpy, toastSpies } = vi.hoisted(() => ({
  runSpy: vi.fn(() =>
    Promise.resolve({
      data: {
        metrics: {
          n_bars: 240,
          final_equity: 10123.45,
          total_return_pct: 1.23,
          max_drawdown: 0.05,
          n_trades: 4,
          win_rate: 0.75,
          exposure: 0.4,
        },
        equity_curve: [
          { ts: '2024-07-01T00:00:00Z', equity: 10000 },
          { ts: '2024-07-01T00:05:00Z', equity: 10123.45 },
        ],
        manifest: { strategy_name: 'sma-cross' },
      },
    })
  ),
  toastSpies: {
    info: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock('../../api', () => ({
  playgroundAPI: {
    run: runSpy,
  },
}));

vi.mock('../Toast', () => ({
  useToast: () => toastSpies,
}));

describe('PlaygroundPanel', () => {
  beforeEach(() => {
    runSpy.mockClear();
    toastSpies.success.mockClear();
    toastSpies.error.mockClear();
  });

  it('submits a run with overlays and renders metrics', async () => {
    const { container, cleanup } = renderWithClient(<PlaygroundPanel />);
    const form = container.querySelector('form.playground-form');
    expect(form).toBeTruthy();

    // enable the flash-crash overlay
    const checkboxes = [...container.querySelectorAll('input[type="checkbox"]')];
    checkboxes[0].click();
    expect(checkboxes[0].checked).toBe(true);

    // actor key must be present to submit
    const keyInput = container.querySelector('input[aria-label="Actor key"]');
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value'
    ).set;
    setter.call(keyInput, 'k'.repeat(32));
    keyInput.dispatchEvent(new Event('input', { bubbles: true }));

    const submit = form.querySelector('button[type="submit"]');
    submit.click();

    await waitForRendered(container, '[data-testid="run-results"]');
    expect(runSpy).toHaveBeenCalledTimes(1);
    const payload = runSpy.mock.calls[0][0];
    expect(payload.symbol).toBe('BTCUSDT');
    expect(payload.window.timestamps).toHaveLength(240);
    expect(payload.overlays).toHaveLength(1);
    expect(payload.overlays[0].type).toBe('gap_injection');
    expect(toastSpies.success).toHaveBeenCalled();
    cleanup();
  });

  it('disables submission without an actor key', () => {
    const { container, cleanup } = renderWithClient(<PlaygroundPanel />);
    const submit = container.querySelector('button[type="submit"]');
    expect(submit.disabled).toBe(true);
    cleanup();
  });

  it('keeps the governed-scope hint visible', () => {
    const source = fs.readFileSync(
      path.resolve('src/components/PlaygroundPanel.jsx'),
      'utf8'
    );
    expect(source).toMatch(/playground\.run/);
    expect(source).toMatch(/ADR-028/);
  });
});


describe('PlaygroundPanel window floor', () => {
  const inputValue = () =>
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;

  const setField = (input, value) => {
    inputValue().call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  };

  beforeEach(() => {
    runSpy.mockClear();
    toastSpies.success.mockClear();
    toastSpies.error.mockClear();
  });

  it('blocks submission below the 60-bar backend floor and explains why', async () => {
    // The backend rejects windows shorter than its minimum with an opaque
    // error; the client must stop the request and say what is wrong instead.
    const { container, cleanup } = renderWithClient(<PlaygroundPanel />);

    const bars = container.querySelector('input[aria-label="Bars"]');
    setField(bars, '30');
    await flushPromises();

    const hint = await waitForRendered(container, '[data-testid="bars-blocked-hint"]');
    expect(hint.textContent).toContain('60');

    // A valid actor key is not enough while the window is too short.
    const keyInput = container.querySelector('input[aria-label="Actor key"]');
    setField(keyInput, 'k'.repeat(32));
    await flushPromises();

    const submit = container.querySelector('button[type="submit"]');
    expect(submit.disabled).toBe(true);
    expect(runSpy).not.toHaveBeenCalled();

    cleanup();
  });

  it('enables the run once the window meets the 60-bar floor', async () => {
    const { container, cleanup } = renderWithClient(<PlaygroundPanel />);

    const bars = container.querySelector('input[aria-label="Bars"]');
    setField(bars, '60');
    const keyInput = container.querySelector('input[aria-label="Actor key"]');
    setField(keyInput, 'k'.repeat(32));
    await flushPromises();

    const submit = container.querySelector('button[type="submit"]');
    expect(submit.disabled).toBe(false);

    submit.click();
    await waitForRendered(container, '[data-testid="run-results"]');

    expect(runSpy).toHaveBeenCalledTimes(1);
    expect(runSpy.mock.calls[0][0].window.timestamps).toHaveLength(60);

    cleanup();
  });
});
