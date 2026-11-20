import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, CheckSquare, ImagePlus, Images, Loader2, MessageSquare, Play, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { isSealedMessage, type PrivateVault } from '../../lib/privateChatCrypto';
import { timeLabel, type TalkMessage } from './chatModel';
import {
  buildGallery,
  countLabel,
  filterGallery,
  groupByDay,
  itemBlobs,
  justifyRows,
  knownAspect,
  stampLabel,
  type GalleryFilter,
  type GalleryItem,
  type Opened,
  type OpenedEntry,
} from './privateGalleryModel';
import { formatDuration } from '../../lib/privateVideoMedia';
import { knownFrom } from '../../lib/galleryImport';
import PrivateGalleryImport from './PrivateGalleryImport';
import PrivateGalleryViewer from './PrivateGalleryViewer';
import PrivateSlideshow from './PrivateSlideshow';
import { errorText } from './privateChatModel';
import { noSave, useNearScreen, usePrivatePicture } from './usePrivatePicture';

/** The gateway answers at most this many messages a page; the gallery reads one page. */
const GALLERY_HISTORY = 200;

function detailMessages(response: unknown): TalkMessage[] {
  const detail = (response as { detail?: { messages?: unknown } } | undefined)?.detail;
  return Array.isArray(detail?.messages) ? (detail.messages as TalkMessage[]) : [];
}

/**
 * The private chat's encrypted gallery: every picture and video clip sent in
 * the chat and every picture added straight to it (Add pictures, an import
 * from Between; clips come in only by being sent),
 * newest first and grouped by day: a sent picture by when it was sent, an
 * added one by when it was taken. Either parent can open
 * one full screen and comment on it; a comment is a sealed message naming the
 * picture, so the server sees only ciphertext (not even how many comments a
 * picture has). Built from the chat's own messages and dropped on lock.
 */
export default function PrivateGallery({
  token,
  vault,
  messages,
  myActorIds,
  initialBlob,
  onClose,
  onChanged,
  onDelete,
}: {
  token: string;
  vault: PrivateVault;
  /** The thread's messages (the newest), merged with the gallery's own longer read. */
  messages: TalkMessage[];
  myActorIds: ReadonlySet<string>;
  /** Open straight on this picture (a comment in the thread was tapped). */
  initialBlob?: string;
  onClose: () => void;
  onChanged: () => void;
  onDelete: (messageId: number, blobs?: string[]) => Promise<void>;
}) {
  const history = useQuery({
    queryKey: ['private-gallery', token],
    queryFn: () => api.getPrivateMessages(token, GALLERY_HISTORY),
    staleTime: 0,
  });
  const all = useMemo(() => {
    const byId = new Map<number, TalkMessage>();
    for (const m of [...detailMessages(history.data), ...messages]) {
      if (typeof m.id === 'number') byId.set(m.id, m);
    }
    return [...byId.values()].sort((a, b) => Number(a.id) - Number(b.id));
  }, [history.data, messages]);

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
  const added = useMemo<OpenedEntry[]>(
    () =>
      (entries.data?.entries ?? [])
        .filter((e) => e.id in openedEntries)
        .map((e) => ({ id: e.id, at: e.at, own: e.own, byName: e.by_name, payload: openedEntries[e.id] })),
    [entries.data, openedEntries],
  );
  const [importing, setImporting] = useState(false);

  const [opened, setOpened] = useState<Record<string, Opened>>({});
  useEffect(() => {
    const todo = all.filter((m) => !(String(m.id) in opened) && isSealedMessage(m.message));
    if (!todo.length) return;
    let live = true;
    void Promise.all(todo.map(async (m) => [String(m.id), (await vault.open(m.message).catch(() => null)) ?? 'unreadable'] as const)).then(
      (pairs) => {
        if (live && vault.unlocked) setOpened((current) => ({ ...current, ...Object.fromEntries(pairs) }));
      },
    );
    return () => {
      live = false;
    };
  }, [all, opened, vault]);

  const items = useMemo(() => buildGallery(all, opened, myActorIds, added), [all, opened, myActorIds, added]);
  const [filter, setFilter] = useState<GalleryFilter>('all');
  const videoCount = useMemo(() => items.filter((item) => item.video).length, [items]);
  // A filter with nothing left in it falls back to everything.
  const effectiveFilter: GalleryFilter =
    (filter === 'videos' && videoCount === 0) || (filter === 'photos' && videoCount === items.length) ? 'all' : filter;
  const shown = useMemo(() => filterGallery(items, effectiveFilter), [items, effectiveFilter]);
  const days = useMemo(() => groupByDay(shown), [shown]);
  const [chosen, setChosen] = useState<string | undefined>(initialBlob);
  // The viewer moves through what is on screen; a picture opened from the thread may be filtered out.
  const viewList = chosen && !shown.some((item) => item.blob === chosen) ? items : shown;
  const viewIndex = chosen ? viewList.findIndex((item) => item.blob === chosen) : -1;
  const viewing = viewIndex >= 0 ? viewList[viewIndex] : undefined;
  const decrypting =
    history.isLoading ||
    entries.isLoading ||
    all.some((m) => isSealedMessage(m.message) && !(String(m.id) in opened)) ||
    (entries.data?.entries ?? []).some((e) => !(e.id in openedEntries));

  // Each picture's shape, learnt when it is decrypted (numbers only), for the justified rows.
  const [aspects, setAspects] = useState<Record<string, number>>({});
  const learnAspect = useCallback((blob: string, aspect: number) => {
    setAspects((current) => (Math.abs((current[blob] ?? 0) - aspect) < 0.01 ? current : { ...current, [blob]: aspect }));
  }, []);
  const aspectOf = (item: GalleryItem) => aspects[item.blob] ?? knownAspect(item) ?? (item.video ? 16 / 9 : 1);

  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const toggleSelected = (blob: string) =>
    setSelected((current) => (current.includes(blob) ? current.filter((b) => b !== blob) : [...current, blob]));
  const stopSelecting = () => {
    setSelecting(false);
    setSelected([]);
  };
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [slideshow, setSlideshow] = useState<{ items: GalleryItem[]; start: number } | null>(null);

  const [gridRef, width] = useWidth<HTMLDivElement>();
  const targetHeight = width < 480 ? 116 : width < 900 ? 160 : 200;

  const refresh = () => {
    onChanged();
    void history.refetch();
  };

  /** Remove a picture that was added to the gallery: its entry, then its encrypted blob. */
  const removeEntry = async (item: GalleryItem, quiet = false) => {
    if (!item.entryId) return;
    try {
      await api.deletePrivateGalleryEntry(token, item.entryId);
      await api.deletePrivateBlob(token, item.blob).catch(() => {});
      setChosen(undefined);
      void entries.refetch();
    } catch (error) {
      if (quiet) throw error;
      toast.error(errorText(error, 'Could not remove the picture'));
    }
  };

  const chosenItems = shown.filter((item) => selectedSet.has(item.blob));
  const deletable = chosenItems.filter((item) => item.own);
  const deleteSelected = async () => {
    setDeleting(true);
    let failed = 0;
    for (const item of deletable) {
      try {
        if (item.entryId) await removeEntry(item, true);
        else await onDelete(item.messageId, itemBlobs(item));
      } catch {
        failed += 1;
      }
    }
    setDeleting(false);
    setConfirmDelete(false);
    stopSelecting();
    void history.refetch();
    if (failed) toast.error(`${failed} could not be deleted. Try again.`);
    else toast.success(`Deleted ${countLabel(deletable)}`);
  };

  const startSlideshow = (list: GalleryItem[], from?: GalleryItem) => {
    if (!list.length) return;
    const start = from ? Math.max(0, list.findIndex((item) => item.blob === from.blob)) : 0;
    setSlideshow({ items: list, start });
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || chosen || importing || slideshow) return;
      if (confirmDelete) setConfirmDelete(false);
      else if (selecting) stopSelecting();
      else onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [chosen, importing, slideshow, confirmDelete, selecting, onClose]);

  const filters: Array<{ value: GalleryFilter; label: string; count: number }> = [
    { value: 'all', label: 'All', count: items.length },
    { value: 'photos', label: 'Photos', count: items.length - videoCount },
    { value: 'videos', label: 'Videos', count: videoCount },
  ];

  return (
    <div className="fixed inset-0 z-[85] flex flex-col bg-slate-950" role="dialog" aria-modal="true" aria-label="Gallery" data-testid="private-gallery">
      <div className="flex shrink-0 items-center gap-1.5 border-b border-white/5 bg-slate-950/90 px-2 pb-2 pt-[max(0.5rem,var(--safe-top))] backdrop-blur">
        {selecting ? (
          <>
            <button type="button" onClick={stopSelecting} aria-label="Stop selecting" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-200 hover:bg-white/10">
              <X size={20} />
            </button>
            <h2 className="min-w-0 flex-1 truncate text-base font-bold text-white" data-testid="private-gallery-selected-count" aria-live="polite">
              {selected.length ? `${selected.length} selected` : 'Select pictures'}
            </h2>
            <button
              type="button"
              onClick={() => setSelected(selected.length === shown.length ? [] : shown.map((item) => item.blob))}
              className="flex min-h-11 shrink-0 items-center rounded-xl px-2.5 text-xs font-semibold text-rose-100 hover:bg-white/10"
            >
              {selected.length === shown.length ? 'Select none' : 'Select all'}
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={onClose} aria-label="Close gallery" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-200 hover:bg-white/10">
              <X size={20} />
            </button>
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-base font-bold text-white">Gallery</h2>
              <p className="truncate text-xs text-slate-400" data-testid="private-gallery-count">
                {countLabel(items)}
              </p>
            </div>
            {items.length > 0 && (
              <>
                <button
                  type="button"
                  onClick={() => startSlideshow(shown)}
                  aria-label="Play a slideshow"
                  title="Slideshow"
                  data-testid="private-gallery-slideshow"
                  className="flex h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-xl px-2 text-xs font-semibold text-rose-100 hover:bg-white/10"
                >
                  <Play size={16} className="fill-current" aria-hidden />
                  <span className="hidden sm:inline">Slideshow</span>
                </button>
                <button
                  type="button"
                  onClick={() => setSelecting(true)}
                  aria-label="Select pictures"
                  title="Select"
                  data-testid="private-gallery-select"
                  className="flex h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-xl px-2 text-xs font-semibold text-rose-100 hover:bg-white/10"
                >
                  <CheckSquare size={16} aria-hidden />
                  <span className="hidden sm:inline">Select</span>
                </button>
              </>
            )}
            <button
              type="button"
              onClick={() => setImporting(true)}
              data-testid="private-gallery-add"
              aria-label="Add pictures"
              className="flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-xl px-2 text-xs font-semibold text-rose-100 hover:bg-white/10"
            >
              <ImagePlus size={16} aria-hidden /> <span className="hidden min-[400px]:inline">Add pictures</span>
            </button>
          </>
        )}
      </div>
      {videoCount > 0 && videoCount < items.length && (
        <div className="flex shrink-0 gap-1.5 overflow-x-auto px-2 pt-2 sm:px-4" role="radiogroup" aria-label="Show">
          {filters.map((f) => (
            <button
              key={f.value}
              type="button"
              role="radio"
              aria-checked={effectiveFilter === f.value}
              onClick={() => setFilter(f.value)}
              data-testid={`private-gallery-filter-${f.value}`}
              className={`flex min-h-11 shrink-0 items-center gap-1.5 rounded-full px-4 text-sm font-semibold transition sm:min-h-9 ${
                effectiveFilter === f.value ? 'bg-rose-600 text-white shadow-lg shadow-rose-900/30' : 'bg-white/5 text-slate-300 hover:bg-white/10'
              }`}
            >
              {f.label}
              <span className={`text-xs tabular-nums ${effectiveFilter === f.value ? 'text-rose-100' : 'text-slate-500'}`}>{f.count}</span>
            </button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-[max(1rem,var(--safe-bottom))] pt-2 sm:px-4">
        {entries.isError && (
          <p className="mb-2 rounded-xl border border-amber-400/20 bg-amber-500/10 p-3 text-sm text-amber-100" role="alert">
            {errorText(entries.error, 'Could not load the pictures added to the gallery.')}
          </p>
        )}
        {history.isError && (
          <p className="mb-2 rounded-xl border border-amber-400/20 bg-amber-500/10 p-3 text-sm text-amber-100" role="alert">
            {errorText(history.error, 'Could not load older pictures; showing the newest.')}
          </p>
        )}
        {!items.length && decrypting && (
          <p className="flex items-center justify-center gap-2 py-12 text-sm text-slate-400">
            <Loader2 size={14} className="animate-spin" /> Decrypting…
          </p>
        )}
        {!items.length && !decrypting && (
          <p className="flex flex-col items-center gap-2 py-12 text-center text-sm text-slate-400">
            <Images size={28} aria-hidden /> No photos yet. Every photo and video sent in this chat appears here, encrypted, and you can add
            pictures without sending them.
          </p>
        )}
        <div ref={gridRef}>
          {days.map((day) => {
            const rows = justifyRows(day.items.map(aspectOf), width, targetHeight, GAP);
            const allChosen = day.items.every((item) => selectedSet.has(item.blob));
            return (
              <section key={day.key} className="mb-5" aria-label={day.label}>
                <div className="sticky top-0 z-[1] -mx-2 mb-1.5 flex items-center gap-2 bg-slate-950/85 px-3 py-1.5 backdrop-blur sm:-mx-4 sm:px-5">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-300">{day.label}</h3>
                  <span className="text-[11px] text-slate-500">{countLabel(day.items)}</span>
                  {selecting && (
                    <button
                      type="button"
                      onClick={() =>
                        setSelected((current) =>
                          allChosen
                            ? current.filter((blob) => !day.items.some((item) => item.blob === blob))
                            : [...new Set([...current, ...day.items.map((item) => item.blob)])],
                        )
                      }
                      className="ml-auto min-h-11 px-1 text-xs font-semibold text-rose-200 sm:min-h-8"
                    >
                      {allChosen ? 'Unselect day' : 'Select day'}
                    </button>
                  )}
                </div>
                <div className="flex flex-col" style={{ gap: GAP }}>
                  {rows.map((row) => {
                    // A full row stretches its tiles over any rounding; the last, short row keeps their shapes.
                    const used = row.indexes.reduce((sum, i) => sum + aspectOf(day.items[i]) * row.height, 0) + GAP * (row.indexes.length - 1);
                    const fill = used >= width - 2;
                    return (
                      <div key={row.indexes[0]} className="flex" style={{ gap: GAP, height: row.height }}>
                        {row.indexes.map((i) => {
                          const item = day.items[i];
                          return (
                            <Tile
                              key={item.blob}
                              token={token}
                              vault={vault}
                              item={item}
                              width={aspectOf(item) * row.height}
                              fill={fill}
                              selecting={selecting}
                              selected={selectedSet.has(item.blob)}
                              onAspect={learnAspect}
                              onOpen={() => (selecting ? toggleSelected(item.blob) : setChosen(item.blob))}
                              onHold={() => {
                                setSelecting(true);
                                setSelected((current) => (current.includes(item.blob) ? current : [...current, item.blob]));
                              }}
                            />
                        );
                      })}
                    </div>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      </div>
      {selecting && selected.length > 0 && (
        <div
          className="flex shrink-0 items-center justify-center gap-2 border-t border-white/10 bg-slate-900/95 px-3 pb-[max(0.75rem,var(--safe-bottom))] pt-3 backdrop-blur"
          data-testid="private-gallery-selection-bar"
        >
          <button
            type="button"
            onClick={() => startSlideshow(chosenItems)}
            data-testid="private-gallery-selection-slideshow"
            className="flex min-h-11 items-center gap-1.5 rounded-2xl bg-rose-600 px-4 text-sm font-semibold text-white shadow-lg shadow-rose-900/30 hover:bg-rose-500"
          >
            <Play size={15} className="fill-white" aria-hidden /> Slideshow
          </button>
          {deletable.length > 0 && (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              data-testid="private-gallery-selection-delete"
              className="flex min-h-11 items-center gap-1.5 rounded-2xl bg-white/10 px-4 text-sm font-semibold text-rose-100 hover:bg-white/15"
            >
              <Trash2 size={15} aria-hidden /> Delete
            </button>
          )}
        </div>
      )}
      {confirmDelete && (
        <div className="fixed inset-0 z-[92] flex items-end justify-center bg-black/60 p-3 sm:items-center" role="alertdialog" aria-modal="true" aria-label="Delete pictures" data-testid="private-gallery-delete-confirm">
          <div className="w-full max-w-sm rounded-3xl border border-white/10 bg-slate-900 p-5 shadow-2xl">
            <p className="text-base font-bold text-white">Delete {countLabel(deletable)} for both of you?</p>
            <p className="mt-1 text-sm text-slate-400">
              {deletable.length < chosenItems.length
                ? `Only the ones you sent or added can be deleted; the other ${chosenItems.length - deletable.length} stay.`
                : 'They are removed from the chat and the gallery on both phones.'}
            </p>
            <div className="mt-4 flex gap-2">
              <button type="button" onClick={() => setConfirmDelete(false)} disabled={deleting} className="min-h-11 flex-1 rounded-2xl bg-white/10 font-semibold text-slate-100 hover:bg-white/15">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void deleteSelected()}
                disabled={deleting}
                data-testid="private-gallery-delete-confirm-yes"
                className="flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-2xl bg-rose-600 font-semibold text-white hover:bg-rose-500 disabled:opacity-60"
              >
                {deleting && <Loader2 size={15} className="animate-spin" aria-hidden />} Delete
              </button>
            </div>
          </div>
        </div>
      )}
      {viewing && (
        <PrivateGalleryViewer
          token={token}
          vault={vault}
          items={viewList}
          index={viewIndex}
          onIndex={(index) => setChosen(viewList[index]?.blob)}
          onClose={() => setChosen(undefined)}
          onChanged={refresh}
          onDelete={async (messageId, blobs) => {
            await onDelete(messageId, blobs);
            if (blobs?.length) setChosen(undefined);
            void history.refetch();
          }}
          onRemoveEntry={(item) => void removeEntry(item)}
          onSlideshow={(item) => {
            setChosen(undefined);
            startSlideshow(viewList, item);
          }}
        />
      )}
      {slideshow && (
        <PrivateSlideshow
          token={token}
          vault={vault}
          items={slideshow.items}
          startIndex={slideshow.start}
          onClose={() => setSlideshow(null)}
        />
      )}
      {importing && (
        <PrivateGalleryImport
          token={token}
          vault={vault}
          known={knownFrom(items)}
          onClose={() => setImporting(false)}
          onAdded={() => void entries.refetch()}
        />
      )}
      {chosen && !viewing && !decrypting && (
        <div className="fixed inset-x-0 bottom-0 z-[90] p-3">
          <p className="rounded-xl border border-white/10 bg-slate-900 p-3 text-center text-sm text-slate-300" role="status">
            That photo is no longer in the chat.
            <button type="button" onClick={() => setChosen(undefined)} className="ml-2 min-h-11 font-semibold text-rose-200 underline">
              Show all
            </button>
          </p>
        </div>
      )}
    </div>
  );
}

const GAP = 4;
const HOLD_MS = 450;

/** The width of an element, kept up to date as the window or the phone turns. */
function useWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [node, setNode] = useState<T | null>(null);
  const [width, setWidth] = useState(() => (typeof window === 'undefined' ? 360 : Math.min(window.innerWidth, 1200) - 16));
  useEffect(() => {
    if (!node) return undefined;
    const measure = () => setWidth(Math.max(120, Math.floor(node.getBoundingClientRect().width)));
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return [setNode, width];
}

/** A soft colour per picture for its placeholder, so a page of loading tiles is not a grey wall. */
function placeholderTint(blob: string): string {
  let hash = 0;
  for (let i = 0; i < blob.length; i += 1) hash = (hash * 33 + blob.charCodeAt(i)) >>> 0;
  return `hsl(${hash % 360} 35% 22%)`;
}

function Tile({
  token,
  vault,
  item,
  width,
  fill,
  selecting,
  selected,
  onAspect,
  onOpen,
  onHold,
}: {
  token: string;
  vault: PrivateVault;
  item: GalleryItem;
  width: number;
  fill: boolean;
  selecting: boolean;
  selected: boolean;
  onAspect: (blob: string, aspect: number) => void;
  onOpen: () => void;
  onHold: () => void;
}) {
  // A gallery can hold years of pictures: only the ones near the screen are fetched and decrypted.
  const [ref, near] = useNearScreen<HTMLButtonElement>();
  const { url, failed } = usePrivatePicture(token, item.blob, item.mime, vault, near);
  const [loadedUrl, setLoadedUrl] = useState('');
  const loaded = Boolean(url) && loadedUrl === url;
  const when = item.entryId ? `taken ${stampLabel(item.when)}` : stampLabel(item.at);
  const holdTimer = useRef<number | undefined>(undefined);
  const held = useRef(false);
  useEffect(() => () => window.clearTimeout(holdTimer.current), []);
  return (
    <button
      ref={ref}
      type="button"
      onClick={() => {
        if (held.current) {
          held.current = false;
          return;
        }
        onOpen();
      }}
      onPointerDown={() => {
        held.current = false;
        window.clearTimeout(holdTimer.current);
        holdTimer.current = window.setTimeout(() => {
          held.current = true;
          onHold();
        }, HOLD_MS);
      }}
      onPointerUp={() => window.clearTimeout(holdTimer.current)}
      onPointerLeave={() => window.clearTimeout(holdTimer.current)}
      onPointerCancel={() => window.clearTimeout(holdTimer.current)}
      onContextMenu={(event) => event.preventDefault()}
      aria-label={`${item.video ? 'Video' : 'Photo'} from ${item.actorName || 'the chat'}, ${when}${item.video?.duration ? `, ${formatDuration(item.video.duration)}` : ''}${item.comments.length ? `, ${item.comments.length} comments` : ''}`}
      aria-pressed={selecting ? selected : undefined}
      style={{ flex: `${fill ? 1 : 0} 1 ${width}px`, backgroundColor: placeholderTint(item.blob) }}
      className={`group relative block h-full min-w-0 overflow-hidden rounded-md transition-transform duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300 ${
        selected ? 'scale-[0.92] rounded-xl ring-2 ring-rose-400' : ''
      }`}
      data-testid="private-gallery-tile"
    >
      {!loaded && !failed && (
        <span
          className="absolute inset-0 motion-safe:animate-[pg-shimmer_1.6s_linear_infinite]"
          style={{ backgroundImage: 'linear-gradient(110deg, transparent 30%, rgba(255,255,255,0.08) 50%, transparent 70%)', backgroundSize: '200% 100%' }}
          aria-hidden
        />
      )}
      {url ? (
        <img
          src={url}
          alt=""
          {...noSave}
          onLoad={(event) => {
            const img = event.currentTarget;
            if (img.naturalWidth > 0 && img.naturalHeight > 0) onAspect(item.blob, img.naturalWidth / img.naturalHeight);
            setLoadedUrl(url);
          }}
          className={`pointer-events-none h-full w-full select-none object-cover transition duration-500 ease-out [-webkit-touch-callout:none] group-hover:scale-[1.03] ${
            loaded ? 'scale-100 opacity-100 blur-0' : 'scale-105 opacity-0 blur-md'
          }`}
        />
      ) : failed ? (
        <span className="flex h-full items-center justify-center p-1 text-center text-[11px] text-slate-400">Could not open</span>
      ) : null}
      <span className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-black/50 to-transparent opacity-80" aria-hidden />
      <span className="absolute bottom-1 left-1.5 text-[10px] font-medium text-white drop-shadow">{timeLabel(item.when)}</span>
      {item.video && (
        <>
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-hidden>
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-black/45 text-white ring-1 ring-white/30 backdrop-blur-sm">
              <Play size={16} className="ml-0.5 fill-white" />
            </span>
          </span>
          {item.video.duration > 0 && (
            <span className="absolute bottom-1 right-1.5 text-[10px] font-medium tabular-nums text-white drop-shadow" data-testid="private-gallery-video-duration">
              {formatDuration(item.video.duration)}
            </span>
          )}
        </>
      )}
      {item.comments.length > 0 && !selecting && (
        <span className="absolute right-1 top-1 flex items-center gap-0.5 rounded-full bg-black/55 px-1.5 py-0.5 text-[10px] font-medium text-white backdrop-blur-sm">
          <MessageSquare size={10} aria-hidden /> {item.comments.length}
        </span>
      )}
      {selecting && (
        <span
          className={`absolute left-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-full border-2 shadow ${
            selected ? 'border-rose-400 bg-rose-500 text-white' : 'border-white/80 bg-black/30'
          }`}
          aria-hidden
        >
          {selected && <Check size={14} strokeWidth={3} />}
        </span>
      )}
    </button>
  );
}
