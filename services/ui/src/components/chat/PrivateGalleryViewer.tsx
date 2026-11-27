import { useCallback, useEffect, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent, type WheelEvent as ReactWheelEvent } from 'react';
import { ChevronLeft, ChevronRight, ImageMinus, Loader2, MapPin, MessageSquare, Pencil, Play, SendHorizontal, Trash2, Undo2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { PrivateVault } from '../../lib/privateChatCrypto';
import { formatDuration } from '../../lib/privateVideoMedia';
import { timeLabel } from './chatModel';
import { itemBlobs, shownPicture, stampLabel, type GalleryItem } from './privateGalleryModel';
import { errorText } from './privateChatModel';
import { usePrivateVideo } from './usePrivateVideo';
import { noSave, usePrivatePicture } from './usePrivatePicture';

const MAX_ZOOM = 4;
const SWIPE_PX = 60;

/** Decrypt a neighbour ahead of time (shared with its tile), so swiping to it is instant. */
function Preload({ token, vault, item }: { token: string; vault: PrivateVault; item?: GalleryItem }) {
  const shown = item ? shownPicture(item) : { blob: '', mime: 'image/jpeg' };
  usePrivatePicture(token, shown.blob, shown.mime, vault, Boolean(item));
  return null;
}

interface View {
  scale: number;
  x: number;
  y: number;
}

const FIT: View = { scale: 1, x: 0, y: 0 };

/**
 * Pinch, double-tap and wheel zoom with panning, and horizontal swipes when
 * not zoomed in. Pointer events only, so a mouse, a pen and fingers all work.
 */
function useZoomSwipe(onSwipe: (direction: 1 | -1) => void, resetKey: string) {
  const [view, setView] = useState<View>(FIT);
  const [drag, setDrag] = useState(0);
  const [lastKey, setLastKey] = useState(resetKey);
  if (lastKey !== resetKey) {
    setLastKey(resetKey);
    setView(FIT);
    setDrag(0);
  }
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const start = useRef<{ view: View; distance: number; mid: { x: number; y: number }; at: { x: number; y: number } } | null>(null);
  const lastTap = useRef(0);

  const zoomAt = useCallback((scale: number, cx = 0, cy = 0) => {
    setView((current) => {
      const next = Math.min(MAX_ZOOM, Math.max(1, scale));
      if (next === 1) return FIT;
      // Keep the point under the finger (cx, cy from the centre) where it is.
      const k = next / current.scale;
      return { scale: next, x: cx - (cx - current.x) * k, y: cy - (cy - current.y) * k };
    });
  }, []);

  const centre = (el: HTMLElement, x: number, y: number) => {
    const box = el.getBoundingClientRect();
    return { x: x - box.left - box.width / 2, y: y - box.top - box.height / 2 };
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    // A button (play, previous, next) or a playing clip's controls keep their own taps:
    // capturing the pointer would send the click to the stage instead.
    if ((event.target as Element | null)?.closest?.('button, video')) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const points = [...pointers.current.values()];
    if (points.length === 2) {
      const [a, b] = points;
      start.current = {
        view,
        distance: Math.hypot(a.x - b.x, a.y - b.y),
        mid: centre(event.currentTarget, (a.x + b.x) / 2, (a.y + b.y) / 2),
        at: { x: 0, y: 0 },
      };
    } else if (points.length === 1) {
      start.current = { view, distance: 0, mid: { x: 0, y: 0 }, at: { x: event.clientX, y: event.clientY } };
    }
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    if (!pointers.current.has(event.pointerId) || !start.current) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const points = [...pointers.current.values()];
    const begun = start.current;
    if (points.length >= 2 && begun.distance > 0) {
      const [a, b] = points;
      const scale = Math.min(MAX_ZOOM, Math.max(1, begun.view.scale * (Math.hypot(a.x - b.x, a.y - b.y) / begun.distance)));
      const k = scale / begun.view.scale;
      setView(scale === 1 ? FIT : { scale, x: begun.mid.x - (begun.mid.x - begun.view.x) * k, y: begun.mid.y - (begun.mid.y - begun.view.y) * k });
      return;
    }
    const dx = event.clientX - begun.at.x;
    const dy = event.clientY - begun.at.y;
    if (begun.view.scale > 1) setView({ ...begun.view, x: begun.view.x + dx, y: begun.view.y + dy });
    else if (Math.abs(dx) > Math.abs(dy)) setDrag(dx);
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLElement>) => {
    const begun = start.current;
    const wasPinch = pointers.current.size >= 2;
    pointers.current.delete(event.pointerId);
    if (pointers.current.size > 0) {
      // One finger of a pinch lifted: carry on panning with the other.
      const [rest] = [...pointers.current.values()];
      start.current = { view, distance: 0, mid: { x: 0, y: 0 }, at: rest };
      return;
    }
    start.current = null;
    if (!begun || wasPinch) return;
    const dx = event.clientX - begun.at.x;
    const dy = event.clientY - begun.at.y;
    setDrag(0);
    if (begun.view.scale === 1 && Math.abs(dx) > SWIPE_PX && Math.abs(dx) > Math.abs(dy)) {
      onSwipe(dx < 0 ? 1 : -1);
      return;
    }
    if (Math.abs(dx) < 8 && Math.abs(dy) < 8) {
      const now = Date.now();
      if (now - lastTap.current < 300) {
        lastTap.current = 0;
        const at = centre(event.currentTarget, event.clientX, event.clientY);
        if (view.scale > 1) setView(FIT);
        else zoomAt(2.5, at.x, at.y);
      } else {
        lastTap.current = now;
      }
    }
  };

  const onWheel = (event: ReactWheelEvent<HTMLElement>) => {
    if (!event.ctrlKey && view.scale === 1) return;
    const at = centre(event.currentTarget, event.clientX, event.clientY);
    zoomAt(view.scale * (event.deltaY < 0 ? 1.15 : 1 / 1.15), at.x, at.y);
  };

  return {
    view,
    drag,
    zoomed: view.scale > 1,
    reset: () => setView(FIT),
    zoomIn: () => zoomAt(view.scale > 1 ? 1 : 2.5),
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp, onWheel },
  };
}

/**
 * One picture or clip full screen: swipe or use the arrows (keys or buttons)
 * to move through the gallery, pinch or double-tap to zoom, and comment on it.
 * Neighbours are decrypted ahead so moving to them is instant.
 */
export default function PrivateGalleryViewer({
  token,
  vault,
  items,
  index,
  onIndex,
  onClose,
  onChanged,
  onDelete,
  onRemoveEntry,
  onRemoveFromGallery,
  onSlideshow,
  onEdit,
  onRevert,
  editing = '',
  editorOpen = false,
}: {
  token: string;
  vault: PrivateVault;
  /** What the viewer moves through (the gallery as filtered). */
  items: GalleryItem[];
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
  onChanged: () => void;
  onDelete: (messageId: number, blobs?: string[]) => Promise<void>;
  onRemoveEntry: (item: GalleryItem) => void;
  /** Take a picture sent in the chat out of the gallery (it stays in the chat). */
  onRemoveFromGallery?: (item: GalleryItem) => void;
  onSlideshow: (item: GalleryItem) => void;
  /** Edit a photo (always from its original): the edit is saved beside it, the original kept. */
  onEdit?: (item: GalleryItem) => void;
  /** Show an edited photo's original again. */
  onRevert?: (item: GalleryItem) => void;
  /** The blob of the photo whose edit is being opened or saved. */
  editing?: string;
  /** The editor is open over the viewer: its keys are its own. */
  editorOpen?: boolean;
}) {
  const item = items[index];
  const prev = index > 0 ? items[index - 1] : undefined;
  const next = index < items.length - 1 ? items[index + 1] : undefined;
  const [entering, setEntering] = useState<1 | -1 | 0>(0);
  const go = useCallback(
    (direction: 1 | -1) => {
      const target = index + direction;
      if (target < 0 || target >= items.length) return;
      setEntering(direction);
      onIndex(target);
    },
    [index, items.length, onIndex],
  );
  const zoom = useZoomSwipe(go, item?.blob ?? '');
  const shown = item ? shownPicture(item) : { blob: '', mime: 'image/jpeg' };
  const { url, failed } = usePrivatePicture(token, shown.blob, shown.mime, vault, Boolean(item));
  // A clip is decrypted only once its play button is pressed, and dropped when the viewer leaves it.
  const [playingBlob, setPlayingBlob] = useState('');
  const playing = Boolean(item?.video) && playingBlob === item?.blob;
  const clip = usePrivateVideo(token, playing && item?.video ? item.video : null, vault);
  const kind = item?.video ? 'video' : 'photo';
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [showComments, setShowComments] = useState(true);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (editorOpen) return;
      const target = event.target as HTMLElement | null;
      const inField = target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA';
      if (event.key === 'Escape') onClose();
      else if (inField) return;
      else if (event.key === 'ArrowRight') go(1);
      else if (event.key === 'ArrowLeft') go(-1);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, go, editorOpen]);

  if (!item) return null;

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

  const transform = `translate3d(${zoom.view.x + zoom.drag}px, ${zoom.view.y}px, 0) scale(${zoom.view.scale})`;
  const moving = zoom.drag !== 0;

  return (
    <div
      className="fixed inset-0 z-[90] flex flex-col bg-black"
      role="dialog"
      aria-modal="true"
      aria-label={item.video ? 'Video' : 'Photo'}
      data-testid="private-gallery-viewer"
      {...noSave}
    >
      <Preload token={token} vault={vault} item={prev} />
      <Preload token={token} vault={vault} item={next} />
      <div className="flex shrink-0 items-center gap-2 px-2 pb-2 pt-[max(0.5rem,var(--safe-top))]">
        <button type="button" onClick={onClose} aria-label="Back to the gallery" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20">
          <ChevronLeft size={20} />
        </button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-white">
            {item.own ? 'You' : item.actorName || 'Your partner'}
            <span className="ml-2 text-xs font-normal tabular-nums text-slate-400" data-testid="private-gallery-position">
              {index + 1} of {items.length}
            </span>
          </p>
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
              {item.takenAt && Math.abs(item.takenAt - item.at) > 60 && <p className="truncate text-xs text-slate-400">Taken {stampLabel(item.takenAt)}</p>}
            </>
          )}
        </div>
        <button
          type="button"
          onClick={() => onSlideshow(item)}
          aria-label="Slideshow from this one"
          title="Slideshow from this one"
          data-testid="private-gallery-viewer-slideshow"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
        >
          <Play size={17} className="ml-0.5" />
        </button>
        {!item.video && onEdit && (
          <button
            type="button"
            onClick={() => onEdit(item)}
            disabled={Boolean(editing)}
            aria-label={item.edit ? 'Edit again, from the original' : 'Edit this photo'}
            title={item.edit ? 'Edit again' : 'Edit'}
            data-testid="private-gallery-edit"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20 disabled:opacity-50"
          >
            {editing === item.blob ? <Loader2 size={17} className="animate-spin" /> : <Pencil size={17} />}
          </button>
        )}
        {!item.entryId && onRemoveFromGallery && (
          <button
            type="button"
            onClick={() => onRemoveFromGallery(item)}
            aria-label={`Remove this ${kind} from the gallery (it stays in the chat)`}
            title="Remove from gallery"
            data-testid="private-gallery-remove"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
          >
            <ImageMinus size={17} />
          </button>
        )}
        {item.own && (
          <button
            type="button"
            onClick={() => (item.entryId ? onRemoveEntry(item) : void onDelete(item.messageId, itemBlobs(item)))}
            aria-label={`Delete this ${kind} for both of you`}
            data-testid="private-gallery-delete"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-rose-500/20 text-rose-100 hover:bg-rose-500/30"
          >
            <Trash2 size={17} />
          </button>
        )}
      </div>

      <div
        className="relative flex min-h-0 flex-1 touch-none items-center justify-center overflow-hidden px-2 select-none"
        data-testid="private-gallery-stage"
        {...(item.video && playing ? {} : zoom.handlers)}
      >
        <div
          key={`${item.blob}:${shown.blob}`}
          className={`flex h-full w-full items-center justify-center ${
            entering === 1 ? 'motion-safe:animate-[pg-in-right_220ms_ease-out]' : entering === -1 ? 'motion-safe:animate-[pg-in-left_220ms_ease-out]' : 'motion-safe:animate-[pg-fade_200ms_ease-out]'
          }`}
        >
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
              onClick={() => setPlayingBlob(item.blob)}
              disabled={playing}
              aria-label={item.video.duration ? `Play video, ${formatDuration(item.video.duration)}` : 'Play video'}
              className="relative flex max-h-full max-w-full items-center justify-center"
              data-testid="private-gallery-video-play"
            >
              <img src={url} alt="" {...noSave} className="pointer-events-none max-h-full max-w-full select-none object-contain [-webkit-touch-callout:none]" />
              <span className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-hidden>
                <span className="flex h-16 w-16 items-center justify-center rounded-full bg-black/55 text-white ring-1 ring-white/30 backdrop-blur">
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
            <img
              src={url}
              alt={item.caption || 'Photo'}
              {...noSave}
              data-testid="private-gallery-photo"
              style={{ transform, transition: moving ? 'none' : 'transform 180ms ease-out' }}
              className="pointer-events-none max-h-full max-w-full select-none object-contain will-change-transform [-webkit-touch-callout:none]"
            />
          ) : failed ? (
            <p className="text-sm text-slate-400">This picture could not be opened.</p>
          ) : (
            <Loader2 size={24} className="animate-spin text-white/70" />
          )}
        </div>
        {prev && !zoom.zoomed && (
          <button
            type="button"
            onClick={() => go(-1)}
            aria-label="Previous"
            className="absolute left-2 top-1/2 hidden h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full bg-black/40 text-white ring-1 ring-white/15 backdrop-blur transition hover:bg-black/60 sm:flex"
          >
            <ChevronLeft size={22} />
          </button>
        )}
        {next && !zoom.zoomed && (
          <button
            type="button"
            onClick={() => go(1)}
            aria-label="Next"
            className="absolute right-2 top-1/2 hidden h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full bg-black/40 text-white ring-1 ring-white/15 backdrop-blur transition hover:bg-black/60 sm:flex"
          >
            <ChevronRight size={22} />
          </button>
        )}
        {zoom.zoomed && (
          <button
            type="button"
            onClick={zoom.reset}
            className="absolute bottom-3 left-1/2 flex min-h-11 -translate-x-1/2 items-center rounded-full bg-black/60 px-4 text-xs font-semibold text-white ring-1 ring-white/20 backdrop-blur"
            data-testid="private-gallery-zoom-reset"
          >
            {Math.round(zoom.view.scale * 100)}% · Fit
          </button>
        )}
      </div>

      <div className="shrink-0 border-t border-white/10 bg-slate-950/95">
        {item.edit && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-white/10 px-3 py-1.5" data-testid="private-gallery-edited">
            <p className="min-w-0 flex-1 text-xs text-slate-300">
              Edited{item.edit.own ? ' by you' : item.edit.byName ? ` by ${item.edit.byName}` : ''}. The original is kept.
            </p>
            {onEdit && (
              <button
                type="button"
                onClick={() => onEdit(item)}
                disabled={Boolean(editing)}
                data-testid="private-gallery-edit-again"
                className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs font-semibold text-rose-100 hover:bg-white/10 disabled:opacity-50"
              >
                <Pencil size={13} aria-hidden /> Edit again
              </button>
            )}
            {onRevert && (
              <button
                type="button"
                onClick={() => onRevert(item)}
                disabled={Boolean(editing)}
                data-testid="private-gallery-revert"
                className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs font-semibold text-rose-100 hover:bg-white/10 disabled:opacity-50"
              >
                <Undo2 size={13} aria-hidden /> Revert to original
              </button>
            )}
          </div>
        )}
        <button
          type="button"
          onClick={() => setShowComments((v) => !v)}
          aria-expanded={showComments}
          className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-xs font-semibold text-slate-300 hover:bg-white/5"
        >
          <MessageSquare size={14} aria-hidden />
          {item.comments.length ? `${item.comments.length} comment${item.comments.length === 1 ? '' : 's'}` : 'Comments'}
          <span className="ml-auto text-slate-500">{showComments ? 'Hide' : 'Show'}</span>
        </button>
        {showComments && (
          <div className="max-h-[30vh] overflow-y-auto px-3 pb-2" data-testid="private-gallery-comments">
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
        )}
        <form onSubmit={(event) => void comment(event)} className="flex items-center gap-2 px-2 pb-[max(0.5rem,var(--safe-bottom))] pt-1">
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
    </div>
  );
}
