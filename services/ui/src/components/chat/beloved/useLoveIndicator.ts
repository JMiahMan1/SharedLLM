import { useCallback, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../services/api';
import { latestLove, unseenLove } from '../../../lib/beloved/records';

const SEEN_PREFIX = 'beloved-seen:';

function readSeen(token: string): number {
  try {
    return Number(window.localStorage.getItem(SEEN_PREFIX + token)) || 0;
  } catch {
    return 0;
  }
}

function writeSeen(token: string, at: number): void {
  try {
    window.localStorage.setItem(SEEN_PREFIX + token, String(at));
  } catch {
    // Without storage the heart simply shows again next time.
  }
}

export interface LoveIndicator {
  /** Hearts and love notes from the other spouse not yet seen in Beloved. */
  unseen: number;
  /** Beloved was opened: everything that has arrived so far counts as seen. */
  markSeen: () => void;
}

/**
 * The heart on the private chat bar. It shares Beloved's query (same key, so
 * one request serves both) and counts from what the server lists in the clear
 * (kind, sender, time); nothing is decrypted for it. What was seen is kept on
 * this device as the server time of the newest one, never the content, so a
 * phone whose clock is off still counts right. Only asked for when the viewer
 * is one of the couple.
 */
export function useLoveIndicator(token: string, enabled: boolean): LoveIndicator {
  const query = useQuery({
    queryKey: ['beloved', token],
    queryFn: () => api.getBeloved(token),
    enabled,
    retry: false,
    refetchInterval: enabled ? 30_000 : false,
  });
  const [seen, setSeen] = useState(() => readSeen(token));
  const records = query.data?.records;
  const unseen = enabled && records ? unseenLove(records, seen) : 0;

  const markSeen = useCallback(() => {
    const latest = records ? latestLove(records) : 0;
    if (latest <= seen) return;
    writeSeen(token, latest);
    setSeen(latest);
  }, [records, seen, token]);

  return { unseen, markSeen };
}
