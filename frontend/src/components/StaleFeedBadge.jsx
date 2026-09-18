/**
 * StaleFeedBadge - marker rendered when a pushed websocket feed has gone quiet.
 *
 * Paired with `useStaleFeed`: panels call `markFresh` on every received frame
 * and render this badge once no frame has arrived within STALE_THRESHOLD_MS.
 * It disappears on the next frame.
 *
 * @component
 */

import { STALE_THRESHOLD_MS } from '../hooks/useStaleFeed';

export default function StaleFeedBadge({ testId = 'stale-feed-badge' }) {
  return (
    <span
      className="badge stale-badge"
      role="status"
      data-testid={testId}
      style={{
        backgroundColor: '#fff3cd',
        color: '#856404',
        padding: '4px 10px',
        borderRadius: '12px',
        fontWeight: 600,
        fontSize: '12px',
      }}
    >
      ⏸ Stale feed — no update for {STALE_THRESHOLD_MS / 1000}s
    </span>
  );
}
