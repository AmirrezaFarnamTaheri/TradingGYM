import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, test, vi, it } from 'vitest';
import { renderWithClient, waitForRendered } from '../../test-utils.jsx';

import AuthzPanel from '../AuthzPanel';

const { grantsSpy, revokeSpy, toastSpies } = vi.hoisted(() => ({
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
    health: vi.fn(() => Promise.resolve({ data: { ledger_valid: true, entries: 3 } })),
    check: vi.fn(() => Promise.resolve({ data: { allowed: true, reason: 'ok' } })),
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
    revoke.click();

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
