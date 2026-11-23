/**
 * Reading signals are fire-and-forget metadata: `{what}` only, never a record,
 * its id or any of its sealed content. A failed post is swallowed, so a missing
 * server never blocks the page or produces a toast.
 */
import { useEffect, useRef } from 'react';
import { api } from '../../../services/api';
import type { BelovedSeenWhat } from '../../../lib/beloved/activity';

function post(token: string, what: BelovedSeenWhat): void {
  // Deferred so even a mock without this method fails quietly, off the render path.
  void Promise.resolve()
    .then(() => api.belovedSeen(token, what))
    .catch(() => undefined);
}

/** One visit (Beloved opening, or the gallery opening) signals once. */
export function useSeenVisit(token: string, what: 'page' | 'gallery', active = true): void {
  const sent = useRef(false);
  useEffect(() => {
    if (!active) {
      sent.current = false;
      return;
    }
    if (sent.current || !token) return;
    sent.current = true;
    post(token, what);
  }, [active, token, what]);
}

const records = new Set<string>();

/** For tests: forget which expanded records have already been signalled. */
export function resetSeenRecords(): void {
  records.clear();
}

/**
 * The first time a spouse's note, prayer request or idea is shown expanded in
 * this browser session, say so once. Later refetches and tab switches are quiet.
 */
export function seenRecord(token: string, what: 'note' | 'prayer' | 'idea', id: string): boolean {
  const key = `${token}|${what}|${id}`;
  if (records.has(key)) return false;
  records.add(key);
  post(token, what);
  return true;
}
