import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../services/api';
import type { PrivateVault } from '../../../lib/privateChatCrypto';
import { fetchRef } from '../../../lib/beloved/content';
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
  loading: boolean;
  error: unknown;
  me: string;
  couple: CoupleMember[];
  spouse?: CoupleMember;
  /** The caller's children as Jarvis knows them, to fill in their birthdays. */
  family: FamilyChild[];
  view: BelovedView;
  /** Seal a record on this device and keep it (a new one, or a replacement of `id`). */
  save: (kind: BelovedKind, record: BelovedRecord, options?: { id?: string; notify?: boolean }) => Promise<{ notified: boolean }>;
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
  const refresh = useCallback(() => void refetch(), [refetch]);

  const save = useCallback(
    async (kind: BelovedKind, record: BelovedRecord, options: { id?: string; notify?: boolean } = {}) => {
      const sealed = await vault.sealRecord(record);
      let notified = false;
      if (options.id) await api.replaceBelovedRecord(token, kind, options.id, sealed);
      else notified = (await api.addBelovedRecord(token, kind, sealed, Boolean(options.notify))).notified;
      await refetch();
      return { notified };
    },
    [token, vault, refetch],
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
    // Only the first opening shows a spinner; a record arriving later must not unmount what is being typed.
    loading: query.isLoading || (decrypting && Object.keys(opened).length === 0 && records.length > 0),
    error: query.error,
    me: query.data?.me ?? '',
    couple,
    spouse: couple.find((p) => !p.me),
    family: query.data?.children ?? [],
    view,
    save,
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
