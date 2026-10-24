import { useEffect, useState } from 'react';
import { livePosition } from '../store/ingest';
import type { RemotePlayer } from '../types';

/** How often a running clock re-renders: four times a second is smooth enough. */
export const LIVE_POSITION_INTERVAL_MS = 250;

type Anchor = Pick<RemotePlayer, 'status' | 'positionSec' | 'positionUpdatedAt' | 'item'>;

/**
 * The playhead for a position anchor, re-rendering only the component that
 * asks (the scrubber and its times), 4 Hz while playing and not at all
 * otherwise. The page around it never re-renders for the clock.
 */
export function useLivePosition(anchor: Anchor | null): number {
  const [now, setNow] = useState(() => Date.now());
  const playing = anchor?.status === 'playing';
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = 0;
    const tick = (t: number) => {
      if (t - last >= LIVE_POSITION_INTERVAL_MS) {
        last = t;
        setNow(Date.now());
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing]);
  if (!anchor) return 0;
  return livePosition(anchor, playing ? Math.max(now, anchor.positionUpdatedAt) : now);
}
