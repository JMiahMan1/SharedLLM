import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { useVirtualizer } from '@tanstack/react-virtual';
import { GripVertical, ListMusic, Loader2, Music, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { useOptionalWebPlayer } from '../../../context/webPlayerContext';
import { extractMaImage } from '../../../lib/maImage';
import { proxiedImage } from '../../../lib/mediaImage';
import { useMediaToken } from '../../../lib/mediaToken';
import { formatDuration } from '../format';

const POLL_MS = 5000;
const MAX_ITEMS = 500;
const ROW_HEIGHT = 64;
const VIRTUALIZE_ABOVE = 100;

interface QueueEntry {
  id: string;
  name: string;
  artist: string;
  duration: number;
  /** The raw MA image reference; proxied (and signed) when rendered. */
  image: string | null;
  available: boolean;
}

interface RawQueueItem {
  queue_item_id?: unknown;
  name?: unknown;
  duration?: unknown;
  available?: unknown;
  image?: unknown;
  media_item?: {
    name?: unknown;
    artists?: unknown;
    album?: { name?: unknown } | null;
    image?: unknown;
  } | null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function artistNames(media: RawQueueItem['media_item']): string {
  if (!media) return '';
  if (Array.isArray(media.artists)) {
    return media.artists
      .map((artist) => {
        if (typeof artist === 'string') return artist;
        const record = asRecord(artist);
        return typeof record?.name === 'string' ? record.name : '';
      })
      .filter(Boolean)
      .join(', ');
  }
  return typeof media.album?.name === 'string' ? media.album.name : '';
}

function toEntry(raw: unknown): QueueEntry | null {
  const item = asRecord(raw) as RawQueueItem | null;
  if (!item) return null;
  const id = typeof item.queue_item_id === 'string' ? item.queue_item_id : '';
  if (!id) return null;
  const media = item.media_item && typeof item.media_item === 'object' ? item.media_item : null;
  const mediaName = typeof media?.name === 'string' ? media.name.trim() : '';
  const rawName = typeof item.name === 'string' ? item.name.trim() : '';
  const artists = artistNames(media);
  // Music Assistant builds a queue item's name as "Artist - Track", so the
  // primary line can look like it only knows the artist. The media item's own
  // name is the track title; only fall back to the joined name without the
  // artist prefix so a queued song is identifiable.
  const splitName = rawName.includes(' - ') ? rawName.slice(rawName.indexOf(' - ') + 3).trim() : '';
  const name =
    mediaName
    || (splitName && artists && rawName.startsWith(artists) ? splitName : rawName)
    || (typeof media?.name === 'string' && media.name.trim())
    || 'Untitled';
  const duration = Number(item.duration);
  return {
    id,
    name,
    artist: artists,
    duration: Number.isFinite(duration) && duration > 0 ? duration : 0,
    image: extractMaImage(item.image) ?? extractMaImage(media?.image) ?? null,
    available: item.available !== false,
  };
}

function toEntries(raw: unknown): QueueEntry[] {
  const result = raw && typeof raw === 'object' && 'result' in raw
    ? (raw as { result: unknown }).result
    : raw;
  if (!Array.isArray(result)) return [];
  return result.map(toEntry).filter((entry): entry is QueueEntry => entry !== null);
}

function QueueArt({ entry }: { entry: QueueEntry }) {
  useMediaToken();
  const src = proxiedImage(entry.image);
  return src ? (
    <img src={src} alt="" loading="lazy" className="h-10 w-10 shrink-0 rounded-lg object-cover" />
  ) : (
    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white/5 text-slate-500">
      <Music size={17} aria-hidden />
    </span>
  );
}

function SortableRow({
  entry,
  onRemove,
  disabled,
}: {
  entry: QueueEntry;
  onRemove: () => void;
  disabled: boolean;
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id: entry.id,
    disabled: disabled || !entry.available,
  });

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex h-16 items-center gap-2 rounded-xl px-1 ${isDragging ? 'relative z-10 bg-slate-800 shadow-xl' : ''} ${entry.available ? '' : 'opacity-40'}`}
    >
      <button
        type="button"
        ref={setActivatorNodeRef}
        {...attributes}
        {...listeners}
        disabled={disabled || !entry.available}
        aria-label={`Reorder ${entry.name}`}
        className="flex h-10 w-7 shrink-0 cursor-grab touch-none items-center justify-center rounded-lg text-slate-500 hover:bg-white/5 hover:text-slate-300 active:cursor-grabbing disabled:cursor-default disabled:opacity-30"
      >
        <GripVertical size={17} aria-hidden />
      </button>

      <QueueArt entry={entry} />

      <div className="min-w-0 flex-1 pl-1">
        <p className="truncate text-sm font-medium text-white">{entry.name}</p>
        <p className="truncate text-xs text-slate-400">
          {entry.artist || (entry.available ? '\u00a0' : 'Unavailable')}
        </p>
      </div>

      <span className="w-10 shrink-0 text-right text-[11px] tabular-nums text-slate-500">
        {entry.duration > 0 ? formatDuration(entry.duration) : ''}
      </span>

      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`Remove ${entry.name} from the queue`}
        className="flex h-10 w-9 shrink-0 items-center justify-center rounded-lg text-slate-500 hover:bg-white/10 hover:text-white disabled:opacity-30"
      >
        <X size={16} />
      </button>
    </div>
  );
}

/**
 * The play queue for the app's own web player.
 *
 * Opened from the now-playing sheet, so it is a sheet on top of a sheet: the
 * queue can be reordered or trimmed without losing sight of what is playing.
 * Music Assistant owns the queue; every mutation goes through its queue
 * commands and the list is refreshed from `player_queues/items` afterwards.
 */
export default function QueueSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const player = useOptionalWebPlayer();
  const reduceMotion = useReducedMotion();
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const [queue, setQueue] = useState<Record<string, unknown> | null>(null);
  const [items, setItems] = useState<QueueEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const requestSeq = useRef(0);

  const playerId = player?.playerId ?? null;
  const maCommand = player?.maCommand;
  const isPlaying = player?.isPlaying ?? false;

  const load = useCallback(async () => {
    if (!playerId || !maCommand) return;
    const seq = ++requestSeq.current;
    try {
      const [queueResult, itemsResult] = await Promise.all([
        maCommand('player_queues/get', { queue_id: playerId }),
        maCommand('player_queues/items', { queue_id: playerId, limit: MAX_ITEMS, offset: 0 }),
      ]);
      if (seq !== requestSeq.current) return;
      setQueue(asRecord(queueResult));
      setItems(toEntries(itemsResult));
      setError(false);
    } catch {
      if (seq !== requestSeq.current) return;
      setError(true);
    }
  }, [playerId, maCommand]);

  useEffect(() => {
    if (!open || !playerId) return;
    let cancelled = false;
    setLoading(true);
    setConfirmClear(false);
    void load().finally(() => {
      if (!cancelled) setLoading(false);
    });
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      requestSeq.current += 1;
    };
  }, [open, playerId, load]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, onClose]);

  const currentIndex = useMemo(() => {
    const value = queue?.current_index;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    const currentId = asRecord(queue?.current_item)?.queue_item_id;
    if (typeof currentId === 'string') return items.findIndex((item) => item.id === currentId);
    return -1;
  }, [queue, items]);

  const currentEntry = useMemo(() => {
    const currentItem = asRecord(queue?.current_item);
    if (currentItem) {
      const entry = toEntry(currentItem);
      if (entry) return entry;
    }
    return currentIndex >= 0 ? items[currentIndex] ?? null : null;
  }, [queue, currentIndex, items]);

  const upNext = useMemo(
    () => items.filter((_, index) => index > currentIndex),
    [items, currentIndex],
  );

  const virtualize = upNext.length > VIRTUALIZE_ABOVE;
  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Virtual exposes mutable measurement functions by design.
  const virtualizer = useVirtualizer({
    count: upNext.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 6,
    enabled: virtualize,
  });

  const send = useCallback(async (command: string, args: Record<string, unknown>) => {
    if (!maCommand) return;
    setBusy(true);
    try {
      await maCommand(command, args);
      await load();
    } catch {
      toast.error('Could not update the queue');
      await load();
    } finally {
      setBusy(false);
    }
  }, [maCommand, load]);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id || !playerId) return;
    const from = upNext.findIndex((item) => item.id === active.id);
    const to = upNext.findIndex((item) => item.id === over.id);
    if (from < 0 || to < 0 || from === to) return;

    // Rebuild the full list locally so the rows move before MA answers.
    const nextUp = arrayMove(upNext, from, to);
    const prefix = items.slice(0, currentIndex + 1);
    setItems([...prefix, ...nextUp]);
    void send('player_queues/move_item', {
      queue_id: playerId,
      queue_item_id: String(active.id),
      pos_shift: to - from,
    });
  };

  const remove = (entry: QueueEntry) => {
    if (!playerId) return;
    setItems((current) => current.filter((item) => item.id !== entry.id));
    void send('player_queues/delete_item', { queue_id: playerId, item_id_or_index: entry.id });
  };

  const clear = () => {
    if (!playerId) return;
    if (!confirmClear) {
      setConfirmClear(true);
      return;
    }
    setConfirmClear(false);
    setItems([]);
    setQueue((current) => (current ? { ...current, current_item: null, current_index: null } : current));
    void send('player_queues/clear', { queue_id: playerId });
  };

  const row = (entry: QueueEntry) => (
    <SortableRow
      key={entry.id}
      entry={entry}
      disabled={busy}
      onRemove={() => remove(entry)}
    />
  );

  return (
    <AnimatePresence>
      {open && player ? (
        <motion.div
          className="fixed inset-0 z-[70] flex items-end justify-center bg-black/60 sm:items-center sm:p-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.18 }}
          onClick={onClose}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Queue"
            data-testid="queue-sheet"
            data-reduced-motion={reduceMotion ? 'true' : 'false'}
            onClick={(event) => event.stopPropagation()}
            initial={{ y: reduceMotion ? 0 : 48, opacity: reduceMotion ? 0 : 1 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: reduceMotion ? 0 : 48, opacity: 0 }}
            transition={reduceMotion ? { duration: 0 } : { type: 'spring', stiffness: 400, damping: 40 }}
            className="flex max-h-[88dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-3xl border border-white/10 bg-slate-900/95 shadow-2xl shadow-black/60 backdrop-blur-xl safe-area-bottom sm:rounded-3xl"
          >
            <header className="flex items-center justify-between border-b border-white/10 px-4 py-2">
              <div className="flex items-center gap-2">
                <ListMusic size={17} className="text-[var(--media-accent,var(--site-accent-text))]" aria-hidden />
                <h2 className="text-base font-semibold text-white">Queue</h2>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close queue"
                className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10 hover:text-white"
              >
                <X size={19} />
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              {loading && !currentEntry && upNext.length === 0 ? (
                <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-400" role="status">
                  <Loader2 size={16} className="animate-spin" aria-hidden />Loading the queue…
                </div>
              ) : null}

              {error ? (
                <p className="mb-3 rounded-xl border border-[var(--color-border-mid)] bg-[var(--site-card-bg)] px-3 py-2 text-xs text-[var(--site-text)]">
                  Could not load the queue. It will refresh automatically.
                </p>
              ) : null}

              {currentEntry ? (
                <section aria-label="Now playing">
                  <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Now playing</h3>
                  <div className="flex items-center gap-3 rounded-xl border border-[var(--site-accent-border)] bg-[var(--site-accent-soft)] p-2">
                    <QueueArt entry={currentEntry} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-white">{currentEntry.name}</p>
                      <p className="truncate text-xs text-[var(--media-accent,var(--site-accent-text))]">
                        {isPlaying ? 'Playing' : 'Paused'}
                        {currentEntry.artist ? ` · ${currentEntry.artist}` : ''}
                      </p>
                    </div>
                    <span className="w-10 shrink-0 text-right text-[11px] tabular-nums text-[var(--site-text-muted)]">
                      {currentEntry.duration > 0 ? formatDuration(currentEntry.duration) : ''}
                    </span>
                  </div>
                </section>
              ) : null}

              <section aria-label="Up next" className="mt-4">
                <div className="mb-1 flex items-center justify-between">
                  <h3 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">
                    Up next{upNext.length > 0 ? ` · ${upNext.length}` : ''}
                  </h3>
                  {items.length > 0 ? (
                    <button
                      type="button"
                      onClick={clear}
                      disabled={busy}
                      className={`min-h-9 rounded-lg px-2 text-xs ${confirmClear ? 'bg-rose-500/15 text-rose-200' : 'text-slate-400 hover:bg-white/10 hover:text-white'} disabled:opacity-40`}
                    >
                      {confirmClear ? 'Clear queue?' : 'Clear queue'}
                    </button>
                  ) : null}
                </div>

                {upNext.length === 0 && !loading ? (
                  <p className="py-6 text-center text-sm text-slate-500">
                    {currentEntry ? 'Nothing up next.' : 'The queue is empty.'}
                  </p>
                ) : null}

                <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                  <SortableContext items={upNext.map((entry) => entry.id)} strategy={verticalListSortingStrategy}>
                    {virtualize ? (
                      <div ref={scrollRef} className="max-h-[46dvh] overflow-y-auto">
                        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
                          {virtualizer.getVirtualItems().map((virtualRow) => {
                            const entry = upNext[virtualRow.index];
                            return (
                              <div
                                key={entry.id}
                                ref={virtualizer.measureElement}
                                data-index={virtualRow.index}
                                style={{
                                  position: 'absolute',
                                  top: 0,
                                  left: 0,
                                  width: '100%',
                                  transform: `translateY(${virtualRow.start}px)`,
                                }}
                              >
                                {row(entry)}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ) : (
                      <div>{upNext.map(row)}</div>
                    )}
                  </SortableContext>
                </DndContext>
              </section>

              {upNext.length > 1 ? (
                <p className="mt-3 text-center text-[11px] text-slate-500">
                  Drag the handle to reorder. With a keyboard: focus a handle, press space, then use the arrow keys.
                </p>
              ) : null}
            </div>

            {items.length > 0 ? (
              <footer className="border-t border-white/10 px-4 py-2 text-[11px] text-slate-500">
                {items.length} {items.length === 1 ? 'track' : 'tracks'} in the queue
              </footer>
            ) : null}
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
