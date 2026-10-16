import { useCallback, useEffect, useMemo, useState } from 'react';
import { storageGet, storageGetSync, storageSet } from './storage';

/**
 * "Clear" for a list the server keeps: remembers, on this device, the moment
 * the person cleared it, so only newer items show. Nothing is deleted, which
 * is what clearing a dashboard card should mean.
 */
export function useClearedAt(key: string): [string, (iso?: string) => void] {
  const [clearedAt, setClearedAt] = useState(() => storageGetSync(key) ?? '');
  // In the Android app the synchronous read only knows the keys loaded at
  // startup, so read the stored value properly once mounted.
  useEffect(() => {
    let live = true;
    void storageGet(key).then((v) => { if (live && v) setClearedAt((cur) => (v > cur ? v : cur)); });
    return () => { live = false; };
  }, [key]);
  const clear = useCallback((iso?: string) => {
    // Normalised, so stamps written as '+00:00' or 'Z' compare correctly.
    const at = toIso(iso) || new Date().toISOString();
    setClearedAt(at);
    void storageSet(key, at);
  }, [key]);
  return [clearedAt, clear];
}

/** Items stamped after `clearedAt` (ISO strings compare in time order). */
export function newerThan<T>(items: T[], clearedAt: string, stamp: (item: T) => string | undefined): T[] {
  if (!clearedAt) return items;
  return items.filter((item) => toIso(stamp(item)) > clearedAt);
}

function toIso(value: string | undefined): string {
  if (!value) return '';
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? new Date(ms).toISOString() : '';
}

/** How many dismissed ids to remember; older ones have scrolled out of every list. */
const DISMISSED_MAX = 300;

/**
 * Per-item "clear" for the same kind of list: one notice swiped away, on this
 * device. Kept beside `useClearedAt`, which clears everything up to a moment.
 */
export function useDismissed(key: string): [Set<string>, (id: string) => void] {
  const [ids, setIds] = useState<string[]>(() => parseIds(storageGetSync(key)));
  useEffect(() => {
    let live = true;
    void storageGet(key).then((v) => { if (live && v) setIds(parseIds(v)); });
    return () => { live = false; };
  }, [key]);
  const dismiss = useCallback((id: string) => {
    setIds((cur) => {
      const next = [...cur.filter((x) => x !== id), id].slice(-DISMISSED_MAX);
      void storageSet(key, JSON.stringify(next));
      return next;
    });
  }, [key]);
  const dismissed = useMemo(() => new Set(ids), [ids]);
  return [dismissed, dismiss];
}

function parseIds(raw: string | null | undefined): string[] {
  try {
    const v = JSON.parse(raw || '[]');
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}
