import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../services/api';
import type { PrivateVault } from '../../../lib/privateChatCrypto';
import { fetchRef } from '../../../lib/beloved/content';
import { DEFAULT_BELOVED_PREFS, type BelovedActivityEntry, type BelovedPrefs } from '../../../lib/beloved/activity';
import { useSeenVisit } from './seen';
import {
  buildView,
  readRecord,
  type BelovedKind,
  type BelovedRecord,
  type BelovedView,
  type CoupleMember,
  type FamilyChild,
  type Opened,
  type SealedRecord,
} from '../../../lib/beloved/records';

type Decrypted = Opened | 'unreadable';

function recordKey(r: SealedRecord): string {
  return `${r.id}:${r.updated_at}`;
}

export interface BelovedData {
  /** The chat token, for metadata-only reading signals. */
  token: string;
  loading: boolean;
  error: unknown;
  me: string;
  couple: CoupleMember[];
  spouse?: CoupleMember;
  /** The caller's children as Jarvis knows them, to fill in their birthdays. */
  family: FamilyChild[];
  view: BelovedView;
  /** Reading signals from both spouses, newest first: metadata only. */
  activity: BelovedActivityEntry[];
  /** This caller's own Beloved preferences. */
  prefs: BelovedPrefs;
  /** Change this caller's own preferences (share activity, encouragement, hour). */
  setPrefs: (prefs: Partial<BelovedPrefs>) => Promise<void>;
  /** Seal a record on this device and keep it (a new one, or a replacement of `id`). */
  save: (kind: BelovedKind, record: BelovedRecord, options?: { id?: string; notify?: boolean }) => Promise<{ notified: boolean }>;
  /** The one-tap heart, reused by the home page and the "heart back" buttons. */
  sendHeart: () => Promise<{ notified: boolean }>;
  remove: (kind: BelovedKind, id: string) => Promise<void>;
  refresh: () => void;
}

/**
 * Beloved's records for one chat: fetched as ciphertext, opened in memory with
 * the chat's key, and dropped with it (this lives inside the unlocked chat, so
 * a lock unmounts it). Checks for a heart or a note from the other spouse
 * every 30 seconds while it is open.
 */
export function useBeloved(token: string, vault: PrivateVault): BelovedData {
  const query = useQuery({
    queryKey: ['beloved', token],
    queryFn: () => api.getBeloved(token),
    staleTime: 0,
    retry: false,
    refetchInterval: 30_000,
  });
  // A visit is recorded once per opening; the gate lives in a ref so a refetch
  // or a StrictMode remount cannot send a second one.
  useSeenVisit(token, 'page');
  const activityQuery = useQuery({
    queryKey: ['beloved-activity', token],
    queryFn: () => api.belovedActivity(token),
    staleTime: 0,
    retry: false,
    refetchInterval: 30_000,
  });
  const prefsQuery = useQuery({
    queryKey: ['beloved-prefs', token],
    queryFn: () => api.getBelovedPrefs(token),
    staleTime: 0,
    retry: false,
  });
  const [opened, setOpened] = useState<Record<string, Decrypted>>({});
  const records = useMemo(() => query.data?.records ?? [], [query.data]);

  useEffect(() => {
    const todo = records.filter((r) => !(recordKey(r) in opened));
    if (!todo.length) return;
    let live = true;
    void Promise.all(
      todo.map(async (r) => {
        const raw = await vault.openRecord(r.sealed).catch(() => null);
        const record = readRecord(r.kind, raw);
        const value: Decrypted = record
          ? { id: r.id, kind: r.kind, at: r.at, updatedAt: r.updated_at, by: r.by, own: r.own, byName: r.by_name, record }
          : 'unreadable';
        return [recordKey(r), value] as const;
      }),
    ).then((pairs) => {
      if (live && vault.unlocked) setOpened((current) => ({ ...current, ...Object.fromEntries(pairs) }));
    });
    return () => {
      live = false;
    };
  }, [records, opened, vault]);

  const view = useMemo(() => {
    const list: Opened[] = [];
    for (const r of records) {
      const o = opened[recordKey(r)];
      if (o && o !== 'unreadable') list.push(o);
    }
    return buildView(list);
  }, [records, opened]);

  const refetch = query.refetch;
  const refetchActivity = activityQuery.refetch;
  const refresh = useCallback(() => {
    void refetch();
    void refetchActivity();
  }, [refetch, refetchActivity]);

  const save = useCallback(
    async (kind: BelovedKind, record: BelovedRecord, options: { id?: string; notify?: boolean } = {}) => {
      const sealed = await vault.sealRecord(record);
      let notified: boolean;
      if (options.id) {
        // Changes a spouse will notice (an answered prayer, a changed plan or
        // idea) carry notify; quiet, cosmetic edits do not.
        const updated = await api.updateBelovedRecord(token, kind, options.id, sealed, Boolean(options.notify));
        notified = Boolean(updated.notified);
      } else {
        notified = (await api.addBelovedRecord(token, kind, sealed, Boolean(options.notify))).notified;
      }
      await refetch();
      void refetchActivity();
      return { notified };
    },
    [token, vault, refetch, refetchActivity],
  );

  const sendHeart = useCallback(() => save('heart', { b: 'heart' }, { notify: true }), [save]);

  const refetchPrefs = prefsQuery.refetch;
  const setPrefs = useCallback(
    async (prefs: Partial<BelovedPrefs>) => {
      await api.setBelovedPrefs(token, prefs);
      void refetchPrefs();
    },
    [token, refetchPrefs],
  );

  const remove = useCallback(
    async (kind: BelovedKind, id: string) => {
      await api.deleteBelovedRecord(token, kind, id);
      await refetch();
    },
    [token, refetch],
  );

  const couple = query.data?.couple ?? [];
  const decrypting = records.some((r) => !(recordKey(r) in opened));
  return {
    token,
    // Only the first opening shows a spinner; a record arriving later must not unmount what is being typed.
    loading: query.isLoading || (decrypting && Object.keys(opened).length === 0 && records.length > 0),
    error: query.error,
    me: query.data?.me ?? '',
    couple,
    spouse: couple.find((p) => !p.me),
    family: query.data?.children ?? [],
    view,
    activity: activityQuery.data?.activity ?? [],
    prefs: prefsQuery.data ?? DEFAULT_BELOVED_PREFS,
    setPrefs,
    save,
    sendHeart,
    remove,
    refresh,
  };
}

/** The day's verse text, fetched from the family's Bible service (only the reference is in the app). */
export function useVerse(ref: string): { text: string; loading: boolean; failed: boolean } {
  const passage = useQuery({
    queryKey: ['beloved-verse', ref],
    queryFn: () => api.getBiblePassage(fetchRef(ref)),
    staleTime: 24 * 3_600_000,
    retry: 1,
  });
  const text = (passage.data?.verses ?? []).map((v) => v.text).join(' ').trim();
  return { text, loading: passage.isLoading, failed: passage.isError || (!passage.isLoading && !text) };
}
