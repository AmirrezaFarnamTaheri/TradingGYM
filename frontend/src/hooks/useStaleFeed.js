/**
 * Stale-feed marker for websocket-driven metrics.
 *
 * The metrics websocket pushes a frame every second or so; if the feed stalls
 * (broker pause, drained queue, silent disconnect) the last frame keeps
 * rendering as if it were live.  Panels call `markFresh` on every received
 * frame and read `stale` to render an explicit marker once no frame has
 * arrived within the threshold.  The marker clears on the next frame.
 *
 * @module hooks/useStaleFeed
 */

import { useCallback, useEffect, useRef, useState } from 'react';

const STALE_THRESHOLD_MS = 5000;
const STALE_CHECK_INTERVAL_MS = 1000;

export { STALE_THRESHOLD_MS };

/**
 * Track freshness of a pushed feed.
 * @param {number} [thresholdMs=STALE_THRESHOLD_MS] - No-update window.
 * @returns {{stale: boolean, markFresh: function(): void}}
 */
export function useStaleFeed(thresholdMs = STALE_THRESHOLD_MS) {
  const [stale, setStale] = useState(false);
  const lastFrameRef = useRef(0);

  const markFresh = useCallback(() => {
    lastFrameRef.current = Date.now();
    setStale(false);
  }, []);

  useEffect(() => {
    const tick = () => {
      const lastFrame = lastFrameRef.current;
      // No frame yet (or only old frames): flag the feed once it has ever
      // delivered something and since gone quiet.
      setStale(lastFrame !== 0 && Date.now() - lastFrame > thresholdMs);
    };
    const intervalId = setInterval(tick, STALE_CHECK_INTERVAL_MS);
    return () => clearInterval(intervalId);
  }, [thresholdMs]);

  return { stale, markFresh };
}
