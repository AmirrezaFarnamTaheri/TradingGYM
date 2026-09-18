import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, test, vi, it, beforeEach } from 'vitest';
import { renderWithClient, waitForRendered } from '../../test-utils.jsx';

import ForgePanel from '../ForgePanel';

/**
 * ForgePanel (ADR-034) contract:
 * - Nothing loads without actor credentials: every gated surface waits, and
 *   the panel says why.
 * - Memory renders the *digest* — warnings/confirmations with support counts
 *   and a net signal — not raw journal rows.
 * - Variant proposal sends the full spec plus the seed, and shows the
 *   returned variants; it never saves or applies anything.
 * - The nomination queue offers Accept/Reject only while a nomination is
 *   open, and the panel keeps the "grants nothing" language visible.
 */

const SPEC = {
  schema_version: 1,
  name: 'SMA crossover',
  description: 'Trend following on a fast/slow simple moving-average cross.',
  timeframe: '5m',
  indicators: [
    { alias: 'sma_fast', name: 'sma', params: { period: 10 } },
    { alias: 'sma_slow', name: 'sma', params: { period: 30 } },
  ],
  entry: { mode: 'all', conditions: [] },
  exit: { mode: 'all', conditions: [] },
  tags: ['trend', 'template'],
};

const { spies, toastSpies } = vi.hoisted(() => {
  const spec = {
    schema_version: 1,
    name: 'SMA crossover',
    description: 'Trend following on a fast/slow simple moving-average cross.',
    timeframe: '5m',
    indicators: [
      { alias: 'sma_fast', name: 'sma', params: { period: 10 } },
      { alias: 'sma_slow', name: 'sma', params: { period: 30 } },
    ],
    entry: { mode: 'all', conditions: [] },
    exit: { mode: 'all', conditions: [] },
    tags: ['trend', 'template'],
  };
  return {
    spies: {
      kernel: vi.fn(() =>
        Promise.resolve({
          data: { available: true, abi_version: 1, detail: 'loaded' },
        })
      ),
      memoryAdvice: vi.fn(() =>
        Promise.resolve({
          data: {
            tags: ['regime:chop'],
            warnings: [{ lesson: 'chop entries bleed fees', support: 4 }],
            confirmations: [{ lesson: 'wide stops held', support: 1 }],
            net_signal: -0.6,
            consulted: 2,
            observations: 5,
            entries: 2,
          },
        })
      ),
      recordExperience: vi.fn(() =>
        Promise.resolve({ data: { support: 2, memory_id: 'abc' } })
      ),
      templates: vi.fn(() =>
        Promise.resolve({ data: { templates: { sma_crossover: spec } } })
      ),
      strategyVariants: vi.fn(() =>
        Promise.resolve({
          data: {
            seed: 7,
            requested: 2,
            variants: [
              {
                name: 'SMA crossover-v1',
                indicators: [
                  { alias: 'sma_fast', name: 'sma', params: { period: 8 } },
                  { alias: 'sma_slow', name: 'sma', params: { period: 33 } },
                ],
              },
              {
                name: 'SMA crossover-v2',
                indicators: [
                  { alias: 'sma_fast', name: 'sma', params: { period: 12 } },
                  { alias: 'sma_slow', name: 'sma', params: { period: 27 } },
                ],
              },
            ],
          },
        })
      ),
      nominations: vi.fn(() =>
        Promise.resolve({
          data: {
            nominations: [
              {
                tool_name: 'funding.rate_monitor',
                rationale: 'funding spikes preceded both stress exits',
                proposed_by: 'agent-axiom1',
                status: 'nominated',
              },
              {
                tool_name: 'old.tool',
                rationale: 'superseded',
                proposed_by: 'agent-axiom1',
                status: 'rejected',
                decided_by: 'operator-1',
              },
            ],
          },
        })
      ),
      nominate: vi.fn(() =>
        Promise.resolve({ data: { status: 'nominated' } })
      ),
      resolveNomination: vi.fn(() =>
        Promise.resolve({ data: { status: 'accepted' } })
      ),
    },
    toastSpies: {
      info: vi.fn(),
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
    },
  };
});

vi.mock('../../api', () => ({ forgeAPI: spies }));
vi.mock('../Toast', () => ({ useToast: () => toastSpies }));

const setValue = (input, value) => {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    'value'
  ).set;
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
};

const selectValue = (select, value) => {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLSelectElement.prototype,
    'value'
  ).set;
  setter.call(select, value);
  select.dispatchEvent(new Event('change', { bubbles: true }));
};

async function credentialedPanel() {
  const rendered = renderWithClient(<ForgePanel />);
  const [actorInput, keyInput] = rendered.container.querySelectorAll(
    'form.credential-row input'
  );
  setValue(actorInput, 'agent-axiom1');
  setValue(keyInput, 'k'.repeat(40));
  return rendered;
}

beforeEach(() => {
  Object.values(spies).forEach((spy) => spy.mockClear());
  Object.values(toastSpies).forEach((spy) => spy.mockClear());
});

describe('ForgePanel credential gate', () => {
  it('fetches nothing gated until actor credentials are entered', async () => {
    const { container, cleanup } = renderWithClient(<ForgePanel />);
    await waitForRendered(container, '[data-testid="kernel-state"]');
    expect(spies.kernel).toHaveBeenCalled(); // ungated diagnostic
    expect(spies.templates).not.toHaveBeenCalled();
    expect(spies.nominations).not.toHaveBeenCalled();
    expect(spies.memoryAdvice).not.toHaveBeenCalled();
    expect(container.textContent).toContain('Enter actor credentials');
    cleanup();
  });

  it('reports which simulation kernel is loaded', async () => {
    const { container, cleanup } = renderWithClient(<ForgePanel />);
    // the badge renders immediately with the reference default, so wait for
    // the query to settle rather than for the element to exist
    const badge = await waitForRendered(
      container,
      '[data-testid="kernel-state"]',
      (node) => node.textContent.includes('Rust')
    );
    expect(badge.textContent).toContain('ADR-035');
    cleanup();
  });
});

describe('ForgePanel memory', () => {
  it('renders the advice digest with support counts, not raw rows', async () => {
    const { container, cleanup } = await credentialedPanel();
    const consult = container.querySelector('form.check-row button');
    consult.click();

    const lists = await waitForRendered(container, '[data-testid="advice-lists"]');
    expect(lists.textContent).toContain('chop entries bleed fees');
    expect(lists.textContent).toContain('×4');
    expect(lists.textContent).toContain('wide stops held');

    const summary = container.querySelector('[data-testid="advice-summary"]');
    expect(summary.textContent).toContain('-0.60');
    expect(summary.textContent).toContain('5 observations');
    expect(spies.memoryAdvice).toHaveBeenCalledWith(
      ['regime:chop', 'symbol:btcusdt'],
      'agent-axiom1',
      'k'.repeat(40)
    );
    cleanup();
  });

  it('records an observation with the queried tags', async () => {
    const { container, cleanup } = await credentialedPanel();
    const lessonInput = container.querySelector('form.record-form input');
    setValue(lessonInput, 'chop entries bleed fees');
    container.querySelector('form.record-form button').click();

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(spies.recordExperience).toHaveBeenCalledWith(
      {
        context_tags: ['regime:chop', 'symbol:btcusdt'],
        outcome: 'loss',
        lesson: 'chop entries bleed fees',
      },
      'agent-axiom1',
      'k'.repeat(40)
    );
    expect(toastSpies.success).toHaveBeenCalled();
    cleanup();
  });

  it('refuses to record an empty lesson', async () => {
    const { container, cleanup } = await credentialedPanel();
    const button = container.querySelector('form.record-form button');
    expect(button.disabled).toBe(true);
    cleanup();
  });
});

describe('ForgePanel strategy development', () => {
  it('proposes variants from a selected family and lists them', async () => {
    const { container, cleanup } = await credentialedPanel();
    const select = await waitForRendered(
      container,
      'form.variant-form select option[value="sma_crossover"]'
    );
    expect(select).toBeTruthy();
    selectValue(container.querySelector('form.variant-form select'), 'sma_crossover');

    const detail = await waitForRendered(container, '[data-testid="template-detail"]');
    expect(detail.textContent).toContain('Trend following');
    expect(detail.textContent).toContain('sma');

    container.querySelector('form.variant-form button').click();
    const table = await waitForRendered(container, '[data-testid="variants-table"]');
    expect(table.textContent).toContain('SMA crossover-v1');
    expect(table.textContent).toContain('period=8');

    expect(spies.strategyVariants).toHaveBeenCalledWith(
      { strategy: SPEC, count: 3, seed: 7 },
      'agent-axiom1',
      'k'.repeat(40)
    );
    cleanup();
  });

  it('cannot propose without a selected family', async () => {
    const { container, cleanup } = await credentialedPanel();
    await waitForRendered(container, 'form.variant-form select option[value="sma_crossover"]');
    expect(container.querySelector('form.variant-form button').disabled).toBe(true);
    cleanup();
  });
});

describe('ForgePanel nominations', () => {
  it('offers a decision only while a nomination is open', async () => {
    const { container, cleanup } = await credentialedPanel();
    const table = await waitForRendered(container, '[data-testid="nominations-table"]');
    const rows = table.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelectorAll('button')).toHaveLength(2); // open
    expect(rows[1].querySelectorAll('button')).toHaveLength(0); // resolved
    expect(rows[1].textContent).toContain('operator-1');
    cleanup();
  });

  it('accepts a nomination through the resolve endpoint', async () => {
    const { container, cleanup } = await credentialedPanel();
    const table = await waitForRendered(container, '[data-testid="nominations-table"]');
    table.querySelector('tbody tr button').click();

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(spies.resolveNomination).toHaveBeenCalledWith(
      'funding.rate_monitor',
      { decision: 'accepted' },
      'agent-axiom1',
      'k'.repeat(40)
    );
    cleanup();
  });

  it('nominates a tool with a rationale', async () => {
    const { container, cleanup } = await credentialedPanel();
    const [nameInput, rationaleInput] = container.querySelectorAll(
      'form.nominate-form input'
    );
    setValue(nameInput, 'funding.rate_monitor');
    setValue(rationaleInput, 'funding spikes preceded both stress exits');
    container.querySelector('form.nominate-form button').click();

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(spies.nominate).toHaveBeenCalledWith(
      {
        tool_name: 'funding.rate_monitor',
        rationale: 'funding spikes preceded both stress exits',
      },
      'agent-axiom1',
      'k'.repeat(40)
    );
    cleanup();
  });
});

describe('forge surface language and client contract', () => {
  test('the panel keeps the authority boundary visible', () => {
    const source = fs.readFileSync(
      path.resolve('src/components/ForgePanel.jsx'),
      'utf8'
    );
    expect(source).toMatch(/ADR-034/);
    expect(source).toMatch(/Code is never self-revisable/);
    expect(source).toMatch(/Structure is never mutated/);
  });

  test('the client sends repeated tag parameters, not indexed ones', () => {
    const source = fs.readFileSync(path.resolve('src/api.js'), 'utf8');
    expect(source).toMatch(/forgeAPI/);
    expect(source).toMatch(/indexes: null/);
    expect(source).toMatch(/\/forge\/memory\/advice/);
  });
});
