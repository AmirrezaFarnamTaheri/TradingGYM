import { describe, expect, it, beforeEach, vi } from 'vitest';

import DataPanel from '../DataPanel';
import { renderWithClient, flushPromises, waitForRendered } from '../../test-utils.jsx';

const { downloadSpy, featuresSpy } = vi.hoisted(() => ({
  downloadSpy: vi.fn(() => Promise.resolve({ job_key: 'job-1', pid: 123 })),
  featuresSpy: vi.fn(() => Promise.resolve({ pid: 456 })),
}));

vi.mock('../../api', () => ({
  dataAPI: {
    download: downloadSpy,
    generateFeatures: featuresSpy,
  },
}));

vi.mock('../Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

function windowDays(startDate, endDate) {
  return (new Date(endDate) - new Date(startDate)) / 86_400_000;
}

describe('DataPanel preset downloads', () => {
  beforeEach(() => {
    downloadSpy.mockClear();
    featuresSpy.mockClear();
  });

  it('submits the preset values directly instead of stale form state', async () => {
    // The mutationFn closes over render-time state, so mutate() with no
    // arguments after setState submits whatever the form held before.  Presets
    // must hand their own values to the mutation; the old setTimeout workaround
    // was both racy and still stale, and would not have fired within
    // flushPromises at all.
    const { container, cleanup } = renderWithClient(<DataPanel />);
    await flushPromises();

    const preset = await waitForRendered(
      container,
      'button',
      (btn) => btn.textContent?.includes('Download BTC 5m (1 Year)')
    );
    preset.click();
    await flushPromises();

    expect(downloadSpy).toHaveBeenCalledTimes(1);
    const [symbol, timeframe, startDate, endDate] = downloadSpy.mock.calls[0];
    expect(symbol).toBe('BTC');
    expect(timeframe).toBe('5m');
    expect(startDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(endDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // The form defaults to a six-month window; this preset is one year.
    expect(windowDays(startDate, endDate)).toBeGreaterThan(360);

    cleanup();
  });

  it('passes each preset its own symbol and window', async () => {
    const { container, cleanup } = renderWithClient(<DataPanel />);
    await flushPromises();

    const preset = await waitForRendered(
      container,
      'button',
      (btn) => btn.textContent?.includes('Download ETH 5m (6 Months)')
    );
    preset.click();
    await flushPromises();

    expect(downloadSpy).toHaveBeenCalledTimes(1);
    const [symbol, timeframe, startDate, endDate] = downloadSpy.mock.calls[0];
    expect(symbol).toBe('ETH');
    expect(timeframe).toBe('5m');
    expect(windowDays(startDate, endDate)).toBeGreaterThan(170);
    expect(windowDays(startDate, endDate)).toBeLessThan(190);

    cleanup();
  });

  it('sends the current form selection from the primary download button', async () => {
    const { container, cleanup } = renderWithClient(<DataPanel />);
    await flushPromises();

    const button = await waitForRendered(
      container,
      'button',
      (btn) => btn.textContent?.includes('Download Data')
    );
    button.click();
    await flushPromises();

    expect(downloadSpy).toHaveBeenCalledTimes(1);
    const [symbol, timeframe, startDate, endDate] = downloadSpy.mock.calls[0];
    expect(symbol).toBe('BTC');
    expect(timeframe).toBe('5m');
    // Primary button still uses the live form state (six-month default).
    expect(windowDays(startDate, endDate)).toBeLessThan(190);

    cleanup();
  });
});
