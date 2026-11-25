// The gallery's sealed entries (pictures added straight to it, and the
// membership records of what was put in it from the chat), opened on this
// device, plus the two writes membership needs: an add or remove, and the
// one-time opt-in start. Shared by the thread and the gallery (one query).
import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../services/api';
import { isAnimatedImage } from '../../lib/animatedImage';
import type { PrivateVault } from '../../lib/privateChatCrypto';
import type { TalkMessage } from './chatModel';
import { galleryMembership, markRecords, startRecords, type GalleryMembership, type Opened, type OpenedEntry } from './privateGalleryModel';

export function useGalleryEntries(token: string, vault: PrivateVault) {
  const entries = useQuery({
    queryKey: ['private-gallery-entries', token],
    queryFn: () => api.getPrivateGallery(token),
    staleTime: 0,
  });
  const [openedEntries, setOpenedEntries] = useState<Record<string, Opened>>({});
  useEffect(() => {
    const todo = (entries.data?.entries ?? []).filter((e) => !(e.id in openedEntries));
    if (!todo.length) return;
    let live = true;
    void Promise.all(todo.map(async (e) => [e.id, (await vault.open(e.sealed).catch(() => null)) ?? 'unreadable'] as const)).then((pairs) => {
      if (live && vault.unlocked) setOpenedEntries((current) => ({ ...current, ...Object.fromEntries(pairs) }));
    });
    return () => {
      live = false;
    };
  }, [entries.data, openedEntries, vault]);
  const opened = useMemo<OpenedEntry[]>(
    () =>
      (entries.data?.entries ?? [])
        .filter((e) => e.id in openedEntries)
        .map((e) => ({ id: e.id, at: e.at, own: e.own, byName: e.by_name, payload: openedEntries[e.id] })),
    [entries.data, openedEntries],
  );
  const opening = entries.isLoading || (entries.data?.entries ?? []).some((e) => !(e.id in openedEntries));
  return { query: entries, opened, opening };
}

export function useMembership(messages: TalkMessage[], opened: Record<string, Opened>, entries: OpenedEntry[]): GalleryMembership {
  return useMemo(() => galleryMembership(messages, opened, entries), [messages, opened, entries]);
}

/** Put sent pictures (by blob; clips by poster) in the gallery, or take them out: sealed, padded entries. */
export async function recordMembership(token: string, vault: PrivateVault, op: 'add' | 'remove', blobs: string[]): Promise<void> {
  for (const record of markRecords(op, blobs)) await api.addPrivateGalleryEntry(token, await vault.seal(record));
}

const starting = new Map<string, Promise<void>>();
/** Chats this device wrote the start for in this session (the entries may not have been read back yet). */
const written = new Set<string>();

/**
 * Write the gallery's opt-in start once from this device: read each sent
 * picture that might move (GIF, WebP, PNG), keep the ones that are still, and
 * seal the list. A picture that cannot be read is kept (nothing special is
 * lost by a failed download). Both phones may race to write it; the earliest
 * start counts and the lists are merged, so the result is the same.
 */
export function writeGalleryStart(token: string, vault: PrivateVault, candidates: Array<{ blob: string }>): Promise<void> {
  const running = starting.get(token);
  if (running) return running;
  if (written.has(token)) return Promise.resolve();
  const work = (async () => {
    const kept: string[] = [];
    const queue = [...candidates];
    const worker = async () => {
      for (let next = queue.shift(); next; next = queue.shift()) {
        try {
          const plain = await vault.decrypt(await api.getPrivateBlob(token, next.blob));
          const animated = isAnimatedImage(plain);
          plain.fill(0);
          if (!animated) kept.push(next.blob);
        } catch {
          kept.push(next.blob);
        }
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    for (const record of startRecords(kept.sort())) await api.addPrivateGalleryEntry(token, await vault.seal(record));
    written.add(token);
  })();
  starting.set(token, work);
  work.finally(() => starting.delete(token)).catch(() => {});
  return work;
}

/** For tests: forget which chats this device started. */
export function resetGalleryStartForTests(): void {
  starting.clear();
  written.clear();
}
