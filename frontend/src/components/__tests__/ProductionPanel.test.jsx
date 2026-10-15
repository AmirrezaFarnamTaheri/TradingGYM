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

const { authoritySpy, emergencySpy, tradingSpy, reconciliationSpy } = vi.hoisted(() => ({
  authoritySpy: vi.fn(),
  emergencySpy: vi.fn(),
  tradingSpy: vi.fn(),
  reconciliationSpy: vi.fn(),
}));

vi.mock('../../api', () => ({
  productionAPI: {
    getAuthority: authoritySpy,
    getEmergencyState: emergencySpy,
    getReconciliation: reconciliationSpy,
  },
  tradingAPI: { getStatus: tradingSpy },
}));

describe('Production operator live status', () => {
  beforeEach(() => {
    authoritySpy.mockReset();
    emergencySpy.mockReset();
    tradingSpy.mockReset();
    reconciliationSpy.mockReset();
    reconciliationSpy.mockResolvedValue({ data: {
      state: 'journal_clear', unresolved_total: 0,
      pending: 0, ambiguous: 0, authority_granted: false,
      can_trade: false,
    } });
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

describe('Production reconciliation visibility', () => {
  beforeEach(() => {
    authoritySpy.mockReset();
    emergencySpy.mockReset();
    tradingSpy.mockReset();
    reconciliationSpy.mockReset();
    authoritySpy.mockResolvedValue({ data: {
      single_live_authority: 'LiveExecutionCoordinator',
      wallet_custody: false,
      operation_specific_authorization: true,
      emergency_halted: false,
      emergency_revision: 1,
    } });
    emergencySpy.mockResolvedValue({ data: {
      halted: false, revision: 1,
    } });
    tradingSpy.mockResolvedValue({ data: { status: 'stopped' } });
  });

  it('makes unresolved journal mutations visible without exposing identifiers', async () => {
    reconciliationSpy.mockResolvedValue({ data: {
      state: 'blocked', unresolved_total: 3, pending: 2, ambiguous: 1,
      authority_granted: false, can_trade: false,
    } });
    const { container, cleanup } = renderWithClient(<ProductionPanel />);
    const tile = await waitForRendered(
      container, '[data-testid="production-reconciliation"]',
      (node) => node.textContent.includes('Broker reconciliation required')
    );
    expect(tile.textContent).toContain('Broker reconciliation required');
    expect(container.textContent).toContain('3 unresolved');
    expect(container.textContent).toContain('2 pending');
    expect(container.textContent).toContain('1 ambiguous');
    expect(container.textContent).not.toContain('Safe to trade');
    cleanup();
  });

  it('never renders a clear broker state if journal inspection fails', async () => {
    reconciliationSpy.mockRejectedValue(new Error('unavailable'));
    const { container, cleanup } = renderWithClient(<ProductionPanel />);
    await waitForRendered(
      container, '[data-testid="production-reconciliation"]',
      (node) => node.textContent.includes('Unverified')
    );
    expect(container.textContent).toContain('Journal evidence unavailable');
    cleanup();
  });
});

it('rejects internally contradictory broker journal snapshots', async () => {
  reconciliationSpy.mockResolvedValue({ data: {
    state: 'journal_clear', unresolved_total: 4, pending: 1, ambiguous: 3,
    authority_granted: false, can_trade: false,
  } });
  authoritySpy.mockResolvedValue({ data: {
    single_live_authority: 'LiveExecutionCoordinator',
    wallet_custody: false, operation_specific_authorization: true,
    emergency_halted: false, emergency_revision: 1,
  } });
  emergencySpy.mockResolvedValue({ data: { halted: false, revision: 1 } });
  tradingSpy.mockResolvedValue({ data: { status: 'stopped' } });
  const { container, cleanup } = renderWithClient(<ProductionPanel />);
  await waitForRendered(
    container, '[data-testid="production-reconciliation"]',
    (node) => node.textContent.includes('Unverified')
  );
  expect(container.textContent).toContain('Journal evidence unavailable');
  cleanup();
});
