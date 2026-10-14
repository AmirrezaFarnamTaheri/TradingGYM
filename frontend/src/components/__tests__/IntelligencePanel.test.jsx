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
});
