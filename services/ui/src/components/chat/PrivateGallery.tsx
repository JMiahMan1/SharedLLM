import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ImagePlus, Images, Loader2, MapPin, MessageSquare, Play, SendHorizontal, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { isSealedMessage, type PrivateVault } from '../../lib/privateChatCrypto';
import { timeLabel, type TalkMessage } from './chatModel';
import { buildGallery, countLabel, groupByDay, itemBlobs, stampLabel, type GalleryItem, type Opened, type OpenedEntry } from './privateGalleryModel';
import { formatDuration } from '../../lib/privateVideoMedia';
import { usePrivateVideo } from './usePrivateVideo';
import { knownFrom } from '../../lib/galleryImport';
import PrivateGalleryImport from './PrivateGalleryImport';
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
  const days = useMemo(() => groupByDay(items), [items]);
  const [chosen, setChosen] = useState<string | undefined>(initialBlob);
  const viewing = chosen ? items.find((item) => item.blob === chosen) : undefined;
  const decrypting =
    history.isLoading ||
    entries.isLoading ||
    all.some((m) => isSealedMessage(m.message) && !(String(m.id) in opened)) ||
    (entries.data?.entries ?? []).some((e) => !(e.id in openedEntries));

  const refresh = () => {
    onChanged();
    void history.refetch();
  };

  /** Remove a picture that was added to the gallery: its entry, then its encrypted blob. */
  const removeEntry = async (item: GalleryItem) => {
    if (!item.entryId) return;
    try {
      await api.deletePrivateGalleryEntry(token, item.entryId);
      await api.deletePrivateBlob(token, item.blob).catch(() => {});
      setChosen(undefined);
      void entries.refetch();
    } catch (error) {
      toast.error(errorText(error, 'Could not remove the picture'));
    }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !chosen && !importing) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [chosen, importing, onClose]);

  return (
    <div className="fixed inset-0 z-[85] flex flex-col bg-slate-950" role="dialog" aria-modal="true" aria-label="Gallery" data-testid="private-gallery">
      <div className="flex shrink-0 items-center gap-2 border-b border-white/5 px-2 pb-2 pt-[max(0.5rem,var(--safe-top))]">
        <button type="button" onClick={onClose} aria-label="Close gallery" className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-200 hover:bg-white/10">
          <X size={20} />
        </button>
        <h2 className="min-w-0 flex-1 truncate text-base font-bold text-white">Gallery</h2>
        <span className="shrink-0 text-xs text-slate-400" data-testid="private-gallery-count">
          {countLabel(items)}
        </span>
        <button
          type="button"
          onClick={() => setImporting(true)}
          data-testid="private-gallery-add"
          className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl px-2.5 text-xs font-semibold text-rose-100 hover:bg-white/10"
        >
          <ImagePlus size={16} aria-hidden /> Add pictures
        </button>
      </div>
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
        {days.map((day) => (
          <section key={day.key} className="mb-4" aria-label={day.label}>
            <h3 className="mb-1.5 px-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{day.label}</h3>
            <div className="grid grid-cols-3 gap-1 sm:grid-cols-4 lg:grid-cols-6">
              {day.items.map((item) => (
                <Tile key={item.blob} token={token} vault={vault} item={item} onOpen={() => setChosen(item.blob)} />
              ))}
            </div>
          </section>
        ))}
      </div>
      {viewing && (
        <Viewer
          token={token}
          vault={vault}
          item={viewing}
          onClose={() => setChosen(undefined)}
          onChanged={refresh}
          onDelete={async (messageId, blobs) => {
            await onDelete(messageId, blobs);
            if (blobs?.length) setChosen(undefined);
            void history.refetch();
          }}
          onRemoveEntry={() => void removeEntry(viewing)}
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

function Tile({ token, vault, item, onOpen }: { token: string; vault: PrivateVault; item: GalleryItem; onOpen: () => void }) {
  // A gallery can hold years of pictures: only the ones near the screen are fetched and decrypted.
  const [ref, near] = useNearScreen<HTMLButtonElement>();
  const { url, failed } = usePrivatePicture(token, item.blob, item.mime, vault, near);
  const when = item.entryId ? `taken ${stampLabel(item.when)}` : stampLabel(item.at);
  return (
    <button
      ref={ref}
      type="button"
      onClick={onOpen}
      aria-label={`${item.video ? 'Video' : 'Photo'} from ${item.actorName || 'the chat'}, ${when}${item.video?.duration ? `, ${formatDuration(item.video.duration)}` : ''}${item.comments.length ? `, ${item.comments.length} comments` : ''}`}
      className="relative block aspect-square overflow-hidden rounded-lg bg-white/5"
      data-testid="private-gallery-tile"
    >
      {url ? (
        <img src={url} alt="" {...noSave} className="pointer-events-none h-full w-full select-none object-cover [-webkit-touch-callout:none]" />
      ) : failed ? (
        <span className="flex h-full items-center justify-center p-1 text-center text-[11px] text-slate-500">Could not open</span>
      ) : (
        <span className="flex h-full items-center justify-center">
          <Loader2 size={16} className="animate-spin text-slate-500" />
        </span>
      )}
      <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[10px] font-medium text-white">{timeLabel(item.when)}</span>
      {item.video && (
        <>
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-hidden>
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-black/55 text-white ring-1 ring-white/30">
              <Play size={16} className="ml-0.5 fill-white" />
            </span>
          </span>
          {item.video.duration > 0 && (
            <span className="absolute bottom-1 right-1 rounded bg-black/60 px-1 text-[10px] font-medium tabular-nums text-white" data-testid="private-gallery-video-duration">
              {formatDuration(item.video.duration)}
            </span>
          )}
        </>
      )}
      {item.comments.length > 0 && (
        <span className="absolute right-1 top-1 flex items-center gap-0.5 rounded bg-black/60 px-1 text-[10px] font-medium text-white">
          <MessageSquare size={10} aria-hidden /> {item.comments.length}
        </span>
      )}
    </button>
  );
}

function Viewer({
  token,
  vault,
  item,
  onClose,
  onChanged,
  onDelete,
  onRemoveEntry,
}: {
  token: string;
  vault: PrivateVault;
  item: GalleryItem;
  onClose: () => void;
  onChanged: () => void;
  onDelete: (messageId: number, blobs?: string[]) => Promise<void>;
  onRemoveEntry: () => void;
}) {
  const { url, failed } = usePrivatePicture(token, item.blob, item.mime, vault);
  // A clip is decrypted only once its play button is pressed, and dropped when the viewer closes.
  const [playing, setPlaying] = useState(false);
  const clip = usePrivateVideo(token, playing && item.video ? item.video : null, vault);
  const kind = item.video ? 'video' : 'photo';
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const comment = async (event: FormEvent) => {
    event.preventDefault();
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      const message = await vault.seal({ t: 'comment', blob: item.blob, text: body, sent_at: Math.floor(Date.now() / 1000) });
      const res = await api.sendPrivateMessage(token, message);
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'The comment was not sent');
      setText('');
      onChanged();
    } catch (error) {
      toast.error(errorText(error, 'The comment was not sent'));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[90] flex flex-col bg-black" role="dialog" aria-modal="true" aria-label={item.video ? 'Video' : 'Photo'} data-testid="private-gallery-viewer" {...noSave}>
      <div className="flex shrink-0 items-center gap-2 px-2 pb-2 pt-[max(0.5rem,var(--safe-top))]">
        <button type="button" onClick={onClose} aria-label="Back to the gallery" className="flex h-11 w-11 items-center justify-center rounded-full bg-white/10 text-white">
          <ChevronLeft size={20} />
        </button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-white">{item.own ? 'You' : item.actorName || 'Your partner'}</p>
          {item.entryId ? (
            <>
              <p className="truncate text-xs text-slate-300" data-testid="private-gallery-stamp">
                {item.takenAt ? `Taken ${stampLabel(item.takenAt)}` : `Added ${stampLabel(item.at)}`}
              </p>
              <p className="truncate text-xs text-slate-400" data-testid="private-gallery-details">
                {[
                  item.details?.source === 'between' ? 'From Between' : 'Added to the gallery',
                  item.name,
                  item.details?.camera,
                  item.details?.width && item.details?.height ? `${item.details.width}×${item.details.height}` : undefined,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              {item.details?.gps && (
                <p className="flex items-center gap-1 truncate text-xs text-slate-400" data-testid="private-gallery-place">
                  <MapPin size={11} aria-hidden /> {item.details.gps.lat.toFixed(4)}, {item.details.gps.lon.toFixed(4)}
                </p>
              )}
            </>
          ) : (
            <>
              <p className="truncate text-xs text-slate-300" data-testid="private-gallery-stamp">
                Sent {stampLabel(item.at)}
              </p>
              {item.takenAt && Math.abs(item.takenAt - item.at) > 60 && (
                <p className="truncate text-xs text-slate-400">Taken {stampLabel(item.takenAt)}</p>
              )}
            </>
          )}
        </div>
        {item.own && (
          <button
            type="button"
            onClick={() => (item.entryId ? onRemoveEntry() : void onDelete(item.messageId, itemBlobs(item)))}
            aria-label={`Delete this ${kind} for both of you`}
            data-testid="private-gallery-delete"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-rose-500/20 text-rose-100"
          >
            <Trash2 size={17} />
          </button>
        )}
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center px-2">
        {item.video && clip.url ? (
          <video
            src={clip.url}
            poster={url || undefined}
            controls
            autoPlay
            playsInline
            controlsList="nodownload noremoteplayback"
            disablePictureInPicture
            disableRemotePlayback
            {...noSave}
            className="h-full w-full select-none object-contain"
            data-testid="private-gallery-video-playing"
          />
        ) : item.video && url ? (
          <button
            type="button"
            onClick={() => setPlaying(true)}
            disabled={playing}
            aria-label={item.video.duration ? `Play video, ${formatDuration(item.video.duration)}` : 'Play video'}
            className="relative flex max-h-full max-w-full items-center justify-center"
            data-testid="private-gallery-video-play"
          >
            <img src={url} alt="" {...noSave} className="pointer-events-none max-h-full max-w-full select-none object-contain [-webkit-touch-callout:none]" />
            <span className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-hidden>
              <span className="flex h-16 w-16 items-center justify-center rounded-full bg-black/55 text-white ring-1 ring-white/30">
                {playing ? <Loader2 size={24} className="animate-spin" /> : <Play size={28} className="ml-1 fill-white" />}
              </span>
            </span>
            {playing && (
              <span className="absolute inset-x-0 bottom-2 text-center text-xs text-white" role={clip.failed ? 'alert' : 'status'}>
                {clip.failed || (clip.progress.count > 1 ? `Decrypting ${clip.progress.done} of ${clip.progress.count}` : 'Decrypting')}
              </span>
            )}
          </button>
        ) : url ? (
          <img src={url} alt={item.caption || 'Photo'} {...noSave} className="pointer-events-none max-h-full max-w-full select-none object-contain [-webkit-touch-callout:none]" />
        ) : failed ? (
          <p className="text-sm text-slate-400">This picture could not be opened.</p>
        ) : (
          <Loader2 size={24} className="animate-spin text-white/70" />
        )}
      </div>
      <div className="max-h-[40%] shrink-0 overflow-y-auto border-t border-white/10 bg-slate-950/95 px-3 py-2" data-testid="private-gallery-comments">
        {item.comments.length === 0 && <p className="py-1 text-xs text-slate-500">No comments yet.</p>}
        {item.comments.map((c) => (
          <div key={c.messageId} className="flex items-start gap-2 py-1" data-testid="private-gallery-comment">
            <p className="min-w-0 flex-1 break-words text-sm text-slate-100">
              <span className="font-semibold text-rose-200">{c.own ? 'You' : c.actorName || 'Your partner'}</span> {c.text}
              <span className="ml-1.5 text-[10px] text-slate-500">{timeLabel(c.at)}</span>
            </p>
            {c.own && (
              <button
                type="button"
                onClick={() => void onDelete(c.messageId)}
                aria-label="Delete this comment"
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-white/10"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        ))}
      </div>
      <form onSubmit={(event) => void comment(event)} className="flex shrink-0 items-center gap-2 bg-slate-950 px-2 pb-[max(0.5rem,var(--safe-bottom))] pt-1">
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={`Comment on this ${kind}`}
          aria-label={`Comment on this ${kind}`}
          data-testid="private-gallery-comment-input"
          className="glass-input min-h-11 min-w-0 flex-1 text-base sm:text-sm"
        />
        <button
          type="submit"
          disabled={sending || !text.trim()}
          aria-label="Send comment"
          data-testid="private-gallery-comment-send"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-rose-600 text-white disabled:opacity-50"
        >
          {sending ? <Loader2 size={17} className="animate-spin" /> : <SendHorizontal size={17} />}
        </button>
      </form>
    </div>
  );
}
