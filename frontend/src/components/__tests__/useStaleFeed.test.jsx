import { describe, expect, it, vi } from 'vitest';

import { STALE_THRESHOLD_MS, useStaleFeed } from '../../hooks/useStaleFeed';
import { renderWithClient, flushPromises } from '../../test-utils.jsx';

function FeedProbe({ threshold }) {
  const { stale, markFresh } = useStaleFeed(threshold);
  return (
    <div>
      <span data-testid="stale-state">{String(stale)}</span>
      <button type="button" data-testid="frame" onClick={markFresh}>frame</button>
    </div>
  );
}

describe('useStaleFeed', () => {
  it('flags a quiet feed past the threshold and clears it on the next frame', async () => {
    // Fake timers are confined to this test body (never a beforeEach) so the
    // interval-driven panels in other files keep real timers.
    vi.useFakeTimers();
    try {
      const { container, cleanup } = renderWithClient(<FeedProbe />);

      const readState = () =>
        container.querySelector('[data-testid="stale-state"]').textContent;

      // No frame has ever arrived, so there is nothing to call stale yet.
      vi.advanceTimersByTime(STALE_THRESHOLD_MS * 2);
      await flushPromises();
      expect(readState()).toBe('false');

      // A frame lands, then the feed goes quiet past the threshold.
      container.querySelector('[data-testid="frame"]').click();
      vi.advanceTimersByTime(STALE_THRESHOLD_MS + 1000);
      await flushPromises();
      expect(readState()).toBe('true');

      // The marker must clear as soon as a fresh frame arrives.
      container.querySelector('[data-testid="frame"]').click();
      await flushPromises();
      expect(readState()).toBe('false');

      cleanup();
    } finally {
      vi.useRealTimers();
    }
  });
});
