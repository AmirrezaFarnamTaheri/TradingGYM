import { describe, expect, it, vi } from 'vitest';
import IntelligencePanel from '../IntelligencePanel';
import { renderWithClient, flushPromises, waitForRendered } from '../../test-utils.jsx';

const { statusSpy } = vi.hoisted(() => ({
  statusSpy: vi.fn(),
}));

vi.mock('../../api', () => ({
  api: { get: statusSpy },
}));

describe('Intelligence model fleet status', () => {
  it('shows limited provider exhaustion and the configured System-1 roster', async () => {
    statusSpy.mockResolvedValueOnce({
      data: {
        mode: 'system1',
        system1_provider_ids: ['fast'],
        system1_memory_enabled: false,
        system1_working_memory_enabled: false,
        llm_provider_id: null,
        selected_hint: 'unavailable',
        note: 'advisory only',
        backends: [
          {
            name: 'llm:fast',
            available: false,
            device: null,
            request_budget: {
              state: 'exhausted', used: 2, ceiling: 2, remaining: 0,
              window_start: 7200,
            },
          },
          { name: 'system1:fast', available: false, device: null },
        ],
        compute: { torch_installed: false },
      },
    });
    const { container, cleanup } = renderWithClient(<IntelligencePanel />);
    await flushPromises();
    await waitForRendered(container, '.intelligence-table');
    expect(container.textContent).toContain('fast');
    expect(container.textContent).toContain('Exhausted');
    expect(container.textContent).toContain('2 / 2');
    expect(container.textContent).toContain('Experience memory');
    expect(container.textContent).toContain('Working-memory P&L disclosure');
    cleanup();
  });

  it('treats failed budget ledger as denied without exposing a path', async () => {
    statusSpy.mockResolvedValueOnce({
      data: {
        mode: 'auto', selected_hint: 'rules',
        note: 'advisory only',
        backends: [
          { name: 'llm:fast', available: false, device: null,
            request_budget: { state: 'unavailable' } },
        ],
      },
    });
    const { container, cleanup } = renderWithClient(<IntelligencePanel />);
    await flushPromises();
    await waitForRendered(container, '.intelligence-table');
    expect(container.textContent).toContain('Budget unavailable');
    expect(container.textContent).not.toContain('private-budget.json');
    cleanup();
  });

  it('can refresh a model-fleet snapshot without silently polling or reloading', async () => {
    const callsBefore = statusSpy.mock.calls.length;
    statusSpy.mockResolvedValueOnce({
      data: { mode: 'system1', note: 'first snapshot', backends: [] },
    });
    statusSpy.mockResolvedValueOnce({
      data: { mode: 'rules', note: 'new snapshot', backends: [] },
    });
    const { container, cleanup } = renderWithClient(<IntelligencePanel />);
    await flushPromises();
    await waitForRendered(container, '.info-box', el => el.textContent.includes('system1'));
    const refresh = await waitForRendered(
      container, 'button', el => el.textContent.includes('Refresh status')
    );
    refresh.click();
    await flushPromises();
    await waitForRendered(container, '.info-box', el => el.textContent.includes('rules'));
    expect(statusSpy.mock.calls.length - callsBefore).toBe(2);
    cleanup();
  });

  it('retries a failed intelligence fetch using the same read-only control', async () => {
    statusSpy.mockRejectedValueOnce(new Error('health temporarily unavailable'));
    statusSpy.mockResolvedValueOnce({
      data: { mode: 'rules', note: 'recovered', backends: [] },
    });
    const { container, cleanup } = renderWithClient(<IntelligencePanel />);
    await flushPromises();
    const error = await waitForRendered(container, '[role="alert"]');
    expect(error.textContent).toContain('temporarily unavailable');
    const refresh = await waitForRendered(
      container, 'button', el => el.textContent.includes('Refresh status')
    );
    refresh.click();
    await flushPromises();
    await waitForRendered(container, '.info-box', el => el.textContent.includes('recovered'));
    expect(container.querySelector('[role="alert"]')).toBeNull();
    cleanup();
  });

  it('shows curated presets as read-only and disabled by default', async () => {
    statusSpy.mockResolvedValueOnce({
      data: {
        mode: 'system1', note: 'advisory only', selected_hint: 'unavailable',
        backends: [],
        provider_presets: [
          {
            preset_id: 'go-systemone', display_name: 'Go System-One',
            kind: 'local', wire_protocol: 'systemone',
            base_url: 'http://127.0.0.1:8080', enabled_by_default: false,
          },
          {
            preset_id: 'openrouter', display_name: 'OpenRouter hosted',
            kind: 'openai_compatible', wire_protocol: 'openai_chat',
            base_url: 'https://openrouter.ai/api/v1',
            enabled_by_default: false,
          },
        ],
      },
    });
    const { container, cleanup } = renderWithClient(<IntelligencePanel />);
    await flushPromises();
    await waitForRendered(container, '.provider-presets');
    expect(container.textContent).toContain('Go System-One');
    expect(container.textContent).toContain('OpenRouter hosted');
    expect(container.textContent).toContain('Disabled by default');
    expect(container.querySelector('.provider-presets button')).toBeNull();
    cleanup();
  });

});

describe('Intelligence status freshness and recovery', () => {
  it('refreshes a stale provider snapshot without reloading the application', async () => {
    statusSpy.mockResolvedValueOnce({
      data: { mode: 'system1', note: 'read-only', backends: [
        { name: 'system1:fast', available: false, device: null },
      ] },
    }).mockResolvedValueOnce({
      data: { mode: 'system1', note: 'read-only', backends: [
        { name: 'system1:fast', available: true, device: null },
      ] },
    });
    const { container, cleanup } = renderWithClient(<IntelligencePanel />);
    await waitForRendered(container, '.intelligence-table');
    expect(container.textContent).toContain('Unavailable');
    const refresh = await waitForRendered(
      container, 'button',
      (button) => button.textContent.includes('Refresh')
    );
    refresh.click();
    await waitForRendered(
      container, '.intelligence-table',
      (table) => table.textContent.includes('Available')
    );
    expect(statusSpy).toHaveBeenCalledTimes(2);
    cleanup();
  });

  it('shows malformed backend data as a recoverable error, never a panel crash', async () => {
    statusSpy.mockResolvedValueOnce({ data: { mode: 'auto', backends: null } });
    const { container, cleanup } = renderWithClient(<IntelligencePanel />);
    const alert = await waitForRendered(container, '[role="alert"]');
    expect(alert.textContent).toContain('Invalid intelligence status');
    expect(container.querySelector('.intelligence-table')).toBeNull();
    cleanup();
  });
});
