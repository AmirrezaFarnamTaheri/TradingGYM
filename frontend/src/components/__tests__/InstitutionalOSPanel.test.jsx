import { act } from 'react-dom/test-utils';
import { describe, expect, it, beforeEach, vi } from 'vitest';

import InstitutionalOSPanel from '../InstitutionalOSPanel';
import { renderWithClient, flushPromises, waitForRendered } from '../../test-utils.jsx';

const { evaluateSpy, artifactsSpy, saveSpy, toastSpies } = vi.hoisted(() => ({
  evaluateSpy: vi.fn(() => Promise.resolve({ data: { verdict: 'review complete' } })),
  artifactsSpy: vi.fn(() => Promise.resolve({ data: [] })),
  saveSpy: vi.fn(() => Promise.resolve({ data: {} })),
  toastSpies: {
    info: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock('../../api', () => ({
  institutionalAPI: {
    evaluate: evaluateSpy,
    artifacts: artifactsSpy,
    saveArtifact: saveSpy,
  },
}));

vi.mock('../Toast', () => ({
  useToast: () => toastSpies,
}));

const CARD_BY_TITLE = (title) => (node) => node.textContent?.includes(title);

describe('InstitutionalOSPanel stale-request guard', () => {
  beforeEach(() => {
    evaluateSpy.mockReset();
    artifactsSpy.mockReset();
    saveSpy.mockReset();
    evaluateSpy.mockResolvedValue({ data: { verdict: 'review complete' } });
    artifactsSpy.mockResolvedValue({ data: [] });
    Object.values(toastSpies).forEach((fn) => fn.mockClear());
  });

  it('drops a stale artefact list that lands after a newer request', async () => {
    // The first artefacts request (default domain) hangs; switching domains
    // bumps the generation counter and starts a newer request that resolves
    // first.  When the slow, older response lands last it must be discarded
    // rather than overwriting the newer list.
    let resolveSlow;
    const slow = new Promise((resolve) => { resolveSlow = resolve; });
    let calls = 0;
    artifactsSpy.mockImplementation(() => {
      calls += 1;
      return calls === 1
        ? slow
        : Promise.resolve({ data: [{ artifact_id: 'newer', revision: 2 }] });
    });

    const { container, cleanup } = renderWithClient(<InstitutionalOSPanel />);
    await flushPromises();

    const card = await waitForRendered(
      container,
      '.status-card',
      CARD_BY_TITLE('Alpha factory')
    );
    card.click();
    await flushPromises();

    expect(container.textContent).toContain('newer');

    resolveSlow({ data: [{ artifact_id: 'older', revision: 1 }] });
    await flushPromises();
    await flushPromises();

    expect(container.textContent).not.toContain('older');
    expect(container.textContent).toContain('newer');

    cleanup();
  });

  it('disables the domain cards and run button while a review is in flight', async () => {
    // A request that never resolves keeps the panel busy: a second click must
    // not be able to start a concurrent request.
    evaluateSpy.mockReturnValue(new Promise(() => {}));

    const { container, cleanup } = renderWithClient(<InstitutionalOSPanel />);
    await flushPromises();

    const run = await waitForRendered(
      container,
      'button',
      (btn) => btn.textContent?.includes('Run bounded review')
    );
    run.click();
    await flushPromises();

    expect(run.disabled).toBe(true);
    const cards = container.querySelectorAll('.status-card');
    expect(cards.length).toBeGreaterThan(0);
    cards.forEach((card) => expect(card.disabled).toBe(true));

    cleanup();
  });

  it('applies the latest review result and clears it on a domain switch', async () => {
    const { container, cleanup } = renderWithClient(<InstitutionalOSPanel />);
    await flushPromises();

    const run = await waitForRendered(
      container,
      'button',
      (btn) => btn.textContent?.includes('Run bounded review')
    );
    run.click();
    await flushPromises();

    expect(container.textContent).toContain('review complete');

    const card = await waitForRendered(
      container,
      '.status-card',
      CARD_BY_TITLE('Alpha factory')
    );
    card.click();
    await flushPromises();

    expect(container.textContent).not.toContain('review complete');

    cleanup();
  });

  it('loads turnover-aware group-cap research example without submitting a review', async () => {
    const { container, cleanup } = renderWithClient(<InstitutionalOSPanel />);
    await flushPromises();

    const portfolio = await waitForRendered(
      container,
      '.status-card',
      CARD_BY_TITLE('Portfolio construction')
    );
    portfolio.click();
    await flushPromises();

    const preset = await waitForRendered(
      container,
      'button',
      (node) => node.textContent?.includes('Load turnover-aware example')
    );
    preset.click();
    await flushPromises();

    const request = JSON.parse(container.querySelector('textarea').value);
    expect(request.method).toBe('turnover-aware');
    expect(request.previous_weights).toEqual({ BTC: 0.7, ETH: 0.3 });
    expect(request.asset_groups).toEqual({
      BTC: 'major-crypto', ETH: 'alternative-crypto',
    });
    expect(request.group_caps['alternative-crypto']).toBe(0.45);
    expect(evaluateSpy).not.toHaveBeenCalled();
    cleanup();
  });

});

function editInstitutionalField(element, value) {
  act(() => {
    const prototype = element.tagName === 'TEXTAREA'
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('Institutional evidence and discovery guardrails', () => {
  beforeEach(() => {
    evaluateSpy.mockReset();
    artifactsSpy.mockReset();
    saveSpy.mockReset();
    evaluateSpy.mockResolvedValue({ data: { verdict: 'review complete' } });
    artifactsSpy.mockResolvedValue({ data: [] });
    Object.values(toastSpies).forEach((fn) => fn.mockClear());
  });

  it('request-edits invalidate completed review evidence before it can be saved', async () => {
    const { container, cleanup } = renderWithClient(<InstitutionalOSPanel />);
    const run = await waitForRendered(container, 'button', button => button.textContent.includes('Run bounded review'));
    run.click();
    await waitForRendered(container, 'pre', node => node.textContent.includes('review complete'));
    editInstitutionalField(container.querySelector('textarea'), '{"market_id":"ETH-USDT"}');
    await flushPromises();
    expect(container.textContent).not.toContain('review complete');
    expect(container.textContent).not.toContain('Save reviewed evidence');
    cleanup();
  });

  it('request-edits invalidate in-flight review responses and re-enable evaluation', async () => {
    let resolveOld;
    evaluateSpy.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    evaluateSpy.mockResolvedValueOnce({ data: { verdict: 'new evidence' } });
    const { container, cleanup } = renderWithClient(<InstitutionalOSPanel />);
    const run = await waitForRendered(container, 'button', button => button.textContent.includes('Run bounded review'));
    run.click();
    await flushPromises();
    expect(run.disabled).toBe(true);
    editInstitutionalField(container.querySelector('textarea'), '{"market_id":"ETH-USDT"}');
    await flushPromises();
    expect(run.disabled).toBe(false);
    run.click();
    await waitForRendered(container, 'pre', node => node.textContent.includes('new evidence'));
    resolveOld({ data: { verdict: 'stale evidence' } });
    await flushPromises();
    expect(container.textContent).not.toContain('stale evidence');
    cleanup();
  });

  it('rejects malformed artifact history instead of crashing the panel', async () => {
    artifactsSpy.mockResolvedValue({ data: { unexpected: 'shape' } });
    const { container, cleanup } = renderWithClient(<InstitutionalOSPanel />);
    const alert = await waitForRendered(container, '[role="alert"]');
    expect(alert.textContent).toContain('Invalid artifact history');
    expect(container.textContent).toContain('No saved reports');
    cleanup();
  });

  it('filters the institutional-domain catalog without mutating the active review', async () => {
    const { container, cleanup } = renderWithClient(<InstitutionalOSPanel />);
    await flushPromises();
    const total = container.querySelectorAll('.status-card').length;
    expect(total).toBeGreaterThan(20);
    const search = container.querySelector('input[aria-label="Search institutional domains"]');
    expect(search).not.toBeNull();
    editInstitutionalField(search, 'market microstructure');
    await flushPromises();
    const cards = Array.from(container.querySelectorAll('.status-card'));
    expect(cards).toHaveLength(1);
    expect(cards[0].textContent).toContain('Market microstructure');
    expect(container.textContent).toContain('1 of');
    expect(container.textContent).toContain('Market regimes');
    cleanup();
  });
});
