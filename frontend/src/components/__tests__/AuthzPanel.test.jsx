import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, test, vi, it } from 'vitest';
import { renderWithClient, flushPromises, waitForRendered } from '../../test-utils.jsx';

import AuthzPanel from '../AuthzPanel';

const { grantsSpy, revokeSpy, healthSpy, checkSpy, toastSpies } = vi.hoisted(() => ({
  grantsSpy: vi.fn(() =>
    Promise.resolve({
      data: {
        grants: [
          {
            grant_id: 'gw-0123456789ab',
            principal: 'agent-axiom1',
            scopes: ['trade.paper.**'],
            expires_at: '2026-10-01T00:00:00Z',
            state: 'active',
          },
        ],
      },
    })
  ),
  revokeSpy: vi.fn(() => Promise.resolve({ data: { state: 'revoked' } })),
  checkSpy: vi.fn(() => Promise.resolve({ data: { allowed: true, reason: 'ok' } })),
  healthSpy: vi.fn(() =>
    Promise.resolve({ data: { ledger_valid: true, entries: 3, legacy_high_risk_wildcard_grants: 0 } })
  ),
  toastSpies: {
    info: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock('../../api', () => ({
  authzAPI: {
    listGrants: grantsSpy,
    health: healthSpy,
    check: checkSpy,
    issue: vi.fn(),
    revoke: revokeSpy,
  },
}));

vi.mock('../Toast', () => ({
  useToast: () => toastSpies,
}));

describe('AuthzPanel', () => {
  it('renders active grants with scopes and revoke buttons', async () => {
    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    const row = await waitForRendered(container, 'table.grants-table tbody tr');
    expect(row).toBeTruthy();
    expect(row.textContent).toContain('agent-axiom1');
    expect(row.textContent).toContain('trade.paper.**');
    const revoke = row.querySelector('button');
    expect(revoke.disabled).toBe(true); // no actor credentials entered yet
    cleanup();
  });

  it('revokes with actor credentials and refreshes', async () => {
    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    await waitForRendered(container, 'table.grants-table tbody tr');
    const [actorInput, keyInput] = container.querySelectorAll(
      'form.credential-row input'
    );
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value'
    ).set;
    setter.call(actorInput, 'operator-1');
    actorInput.dispatchEvent(new Event('input', { bubbles: true }));
    setter.call(keyInput, 'k'.repeat(40));
    keyInput.dispatchEvent(new Event('input', { bubbles: true }));

    const revoke = container.querySelector('table.grants-table tbody button');
    expect(revoke.disabled).toBe(false);
    revokeSpy.mockClear();
    revoke.click();
    expect(revokeSpy).not.toHaveBeenCalled();
    const confirm = await waitForRendered(
      container, 'button',
      (button) => button.textContent?.trim() === 'Confirm revoke'
    );
    confirm.click();

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(revokeSpy).toHaveBeenCalledWith(
      'gw-0123456789ab',
      'operator-1',
      'k'.repeat(40)
    );
    expect(toastSpies.success).toHaveBeenCalled();
    cleanup();
  });
});

describe('AuthzPanel fetch errors', () => {
  it('renders an explicit error state with retry, not an empty grant set', async () => {
    // A failed grants fetch used to fall through to "No active grants", which
    // is indistinguishable from an empty ledger.  It must read as an error.
    grantsSpy.mockRejectedValueOnce(new Error('ledger unavailable'));

    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    await flushPromises();

    const errorBox = await waitForRendered(container, '.error');
    expect(errorBox.textContent).toContain('Failed to load grants');
    expect(errorBox.textContent).toContain('ledger unavailable');
    expect(container.textContent).not.toContain('No active grants');

    // Retrying re-fetches: the mock only rejects once, so the real grant
    // table appears and the error state is gone.
    const retry = await waitForRendered(
      container,
      'button',
      (btn) => btn.textContent?.trim() === 'Retry'
    );
    retry.click();
    await flushPromises();

    await waitForRendered(container, 'table.grants-table tbody tr');
    expect(container.querySelector('.error')).toBeNull();

    cleanup();
  });
});

describe('AuthzPanel health visibility', () => {
  it('never reports valid when the health endpoint is unavailable', async () => {
    healthSpy.mockRejectedValueOnce(new Error('health endpoint offline'));
    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    const ledger = await waitForRendered(
      container,
      '[data-testid="ledger-state"]',
      (node) => node.textContent === 'UNAVAILABLE'
    );
    expect(ledger.textContent).toBe('UNAVAILABLE');
    cleanup();
  });

  it('reports explicitly corrupt ledger state', async () => {
    healthSpy.mockResolvedValueOnce({ data: { ledger_valid: false, entries: 3 } });
    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    const ledger = await waitForRendered(
      container,
      '[data-testid="ledger-state"]',
      (node) => node.textContent === 'CORRUPT'
    );
    expect(ledger.textContent).toBe('CORRUPT');
    cleanup();
  });
});

describe('AuthzPanel malformed responses', () => {
  it('does not display no grants for a malformed server response', async () => {
    grantsSpy.mockResolvedValueOnce({ data: { grants: null } });
    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    const errorBox = await waitForRendered(container, '.error');
    expect(errorBox.textContent).toContain('Invalid grants response');
    expect(container.textContent).not.toContain('No active grants');
    cleanup();
  });
});

describe('session API key storage contract (security review M-4)', () => {
  test('api.js never persists the gateway key to localStorage', () => {
    const source = fs.readFileSync(path.resolve('src/api.js'), 'utf8');
    expect(source).not.toMatch(/localStorage\.setItem\(\s*['"]trading_gym_api_key/);
    expect(source).toMatch(/let sessionApiKey/);
    expect(source).toMatch(/localStorage\.removeItem\(\s*['"]trading_gym_api_key/);
  });

  test('panel sources keep governed-scope language visible', () => {
    const authzSource = fs.readFileSync(
      path.resolve('src/components/AuthzPanel.jsx'),
      'utf8'
    );
    expect(authzSource).toMatch(/Revocation is terminal/);
    expect(authzSource).toMatch(/ADR-028/);
  });
});

describe('Legacy wildcard grant warnings', () => {
  it('makes outstanding high-risk legacy grants visible to operators', async () => {
    healthSpy.mockResolvedValueOnce({
      data: {
        ledger_valid: true,
        entries: 3,
        legacy_high_risk_wildcard_grants: 2,
      },
    });
    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    const banner = await waitForRendered(
      container,
      '[data-testid="legacy-grants-warning"]'
    );
    expect(banner.getAttribute('role')).toBe('alert');
    expect(banner.textContent).toContain('2 legacy high-risk wildcard');
    expect(banner.textContent).toContain('revocation');
    cleanup();
  });

  it('does not interpret absent legacy count as zero risk', async () => {
    healthSpy.mockResolvedValueOnce({ data: { ledger_valid: true, entries: 3 } });
    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    const banner = await waitForRendered(
      container,
      '[data-testid="legacy-grants-unknown"]'
    );
    expect(banner.textContent).toContain('unavailable');
    cleanup();
  });
});

describe('Authorization scope evidence cannot outlive its inputs', () => {
  it('hides a previous ALLOWED result after the scope changes', async () => {
    checkSpy.mockResolvedValueOnce({ data: { allowed: true, reason: 'ok' } });
    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    const scope = container.querySelector('input[aria-label="Check scope"]');
    const checkButton = container.querySelector('form.check-row button');
    checkButton.click();
    await waitForRendered(
      container, '[data-testid="check-result"]',
      (node) => node.textContent.includes('ALLOWED')
    );
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value'
    ).set;
    setter.call(scope, 'trade.live.order.submit');
    scope.dispatchEvent(new Event('input', { bubbles: true }));
    await flushPromises();
    expect(container.querySelector('[data-testid="check-result"]')).toBeNull();
    // Returning to the same input must not resurrect old point-in-time
    // permission evidence without issuing another check.
    setter.call(scope, 'trade.paper.order.submit');
    scope.dispatchEvent(new Event('input', { bubbles: true }));
    await flushPromises();
    expect(container.querySelector('[data-testid="check-result"]')).toBeNull();
    cleanup();
  });

  it('does not display a delayed response after changing the principal', async () => {
    let complete;
    checkSpy.mockImplementationOnce(() => new Promise((resolve) => { complete = resolve; }));
    const { container, cleanup } = renderWithClient(<AuthzPanel />);
    container.querySelector('form.check-row button').click();
    await flushPromises();
    const principal = container.querySelector('input[aria-label="Check principal"]');
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value'
    ).set;
    setter.call(principal, 'another-principal');
    principal.dispatchEvent(new Event('input', { bubbles: true }));
    complete({ data: { allowed: true, reason: 'for_previous_principal' } });
    await flushPromises();
    expect(container.querySelector('[data-testid="check-result"]')).toBeNull();
    cleanup();
  });
});
