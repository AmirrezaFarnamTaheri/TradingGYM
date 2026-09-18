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
});
