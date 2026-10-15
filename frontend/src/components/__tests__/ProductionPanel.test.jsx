import fs from 'node:fs';
import path from 'node:path';
import { beforeEach, describe, expect, it, test, vi } from 'vitest';
import ProductionPanel from '../ProductionPanel';
import { flushPromises, renderWithClient, waitForRendered } from '../../test-utils.jsx';

describe('ProductionPanel authority contract', () => {
  test('keeps live authority and non-custodial language visible', () => {
    const source = fs.readFileSync(path.resolve('src/components/ProductionPanel.jsx'), 'utf8');
    expect(source).toContain('Production Authority');
    expect(source).toMatch(/single production coordinator/i);
    expect(source).toMatch(/non-custodial/i);
  });
});

const { authoritySpy, emergencySpy, tradingSpy } = vi.hoisted(() => ({
  authoritySpy: vi.fn(),
  emergencySpy: vi.fn(),
  tradingSpy: vi.fn(),
}));

vi.mock('../../api', () => ({
  productionAPI: { getAuthority: authoritySpy, getEmergencyState: emergencySpy },
  tradingAPI: { getStatus: tradingSpy },
}));

describe('Production operator live status', () => {
  beforeEach(() => {
    authoritySpy.mockReset();
    emergencySpy.mockReset();
    tradingSpy.mockReset();
    authoritySpy.mockResolvedValue({ data: {
      single_live_authority: 'trading_gym.production.runtime.LiveExecutionCoordinator',
      wallet_custody: false,
      operation_specific_authorization: true,
      emergency_halted: false,
      emergency_revision: 7,
    } });
    emergencySpy.mockResolvedValue({ data: {
      halted: false, revision: 7, reason: '', actor: '', approver: '',
    } });
    tradingSpy.mockResolvedValue({ data: { status: 'stopped', mode: null } });
  });

  it('shows read-only authority evidence without asserting broker qualification', async () => {
    const { container, cleanup } = renderWithClient(<ProductionPanel />);
    await waitForRendered(
      container, '[data-testid="production-emergency"]',
      (node) => node.textContent.includes('Not halted')
    );
    expect(container.textContent).toContain('Revision 7');
    expect(container.textContent).toContain('LiveExecutionCoordinator');
    expect(container.textContent).toContain('not proof of broker reconciliation');
    expect(authoritySpy).toHaveBeenCalled();
    expect(emergencySpy).toHaveBeenCalled();
    expect(tradingSpy).toHaveBeenCalled();
    cleanup();
  });

  it('shows an active halt even if the authority snapshot is unhalted', async () => {
    emergencySpy.mockResolvedValueOnce({ data: {
      halted: true, revision: 7, reason: 'Risk breach',
    } });
    const { container, cleanup } = renderWithClient(<ProductionPanel />);
    await waitForRendered(
      container, '[data-testid="production-emergency"]',
      (node) => node.textContent.includes('Emergency halt active')
    );
    expect(container.textContent).toContain('Risk breach');
    cleanup();
  });

  it('refuses a healthy indication when independent snapshots disagree', async () => {
    emergencySpy.mockResolvedValueOnce({ data: { halted: false, revision: 8 } });
    const { container, cleanup } = renderWithClient(<ProductionPanel />);
    await waitForRendered(
      container, '.production-evidence-warning',
      (node) => node.textContent.includes('snapshots disagree')
    );
    expect(container.querySelector('[data-testid="production-emergency"]').textContent)
      .toContain('Unverified');
    cleanup();
  });

  it('recovers from failed status evidence using read-only refresh', async () => {
    emergencySpy.mockRejectedValueOnce(new Error('offline'));
    const { container, cleanup } = renderWithClient(<ProductionPanel />);
    await waitForRendered(
      container, '.production-evidence-warning',
      (node) => node.textContent.includes('unavailable')
    );
    const refresh = await waitForRendered(
      container, 'button',
      (node) => node.textContent.includes('Refresh')
    );
    refresh.click();
    await flushPromises();
    await waitForRendered(
      container, '[data-testid="production-emergency"]',
      (node) => node.textContent.includes('Not halted')
    );
    expect(emergencySpy).toHaveBeenCalledTimes(2);
    cleanup();
  });
});
