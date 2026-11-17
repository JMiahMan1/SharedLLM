import { useEffect } from 'react';
import { Loader2, Play, Trash2, X } from 'lucide-react';
import type { PrivateVault, VideoPayload } from '../../lib/privateChatCrypto';
import { formatDuration } from '../../lib/privateVideoMedia';
import { noSave, usePrivatePicture } from './usePrivatePicture';
import { ReactionRow } from './PrivateMessageSheet';
import { usePrivateVideo } from './usePrivateVideo';

/** The bubble's box: the clip's own shape, kept between square-ish limits so a tall clip does not fill the thread. */
function aspect(video: Pick<VideoPayload, 'width' | 'height'>): string {
  const w = video.width ?? 16;
  const h = video.height ?? 9;
  const ratio = Math.min(16 / 9, Math.max(3 / 4, w / h));
  return `${ratio}`;
}

/**
 * A video clip in the thread: its decrypted poster frame with a play button
 * and the clip's length. Nothing of the clip itself is fetched until it is
 * opened.
 */
export function PrivateVideoBubble({
  token,
  video,
  vault,
  onOpen,
}: {
  token: string;
  video: VideoPayload;
  vault: PrivateVault;
  onOpen: () => void;
}) {
  const { url, failed } = usePrivatePicture(token, video.poster, 'image/jpeg', vault);
  const length = formatDuration(video.duration);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="relative block w-60 max-w-full overflow-hidden rounded-2xl bg-black/30 text-left"
      style={{ aspectRatio: aspect(video) }}
      aria-label={video.duration ? `Play video, ${length}` : 'Play video'}
      data-testid="private-video"
    >
      {url ? (
        <img
          src={url}
          alt=""
          {...noSave}
          className="pointer-events-none h-full w-full select-none object-cover [-webkit-touch-callout:none] [-webkit-user-drag:none]"
          data-testid="private-video-poster"
        />
      ) : failed ? (
        <span className="flex h-full w-full items-center justify-center p-2 text-center text-xs italic opacity-80">The video's picture could not be opened.</span>
      ) : (
        <span className="flex h-full w-full items-center justify-center">
          <Loader2 size={18} className="animate-spin" />
        </span>
      )}
      <span className="pointer-events-none absolute inset-0 flex items-center justify-center" aria-hidden>
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-black/55 text-white shadow-lg ring-1 ring-white/30">
          <Play size={22} className="ml-0.5 fill-white" />
        </span>
      </span>
      {video.duration > 0 && (
        <span className="pointer-events-none absolute bottom-1.5 right-1.5 rounded-md bg-black/65 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-white" data-testid="private-video-duration">
          {length}
        </span>
      )}
    </button>
  );
}

/**
 * Full-screen player for one clip, inside the chat only: no download, no
 * casting, no picture-in-picture. The clip is decrypted piece by piece while
 * the poster shows, played from memory, and dropped when the player closes.
 */
export function PrivateVideoPlayer({
  token,
  vault,
  video,
  title,
  onClose,
  onDelete,
  onReact,
  mine,
}: {
  token: string;
  vault: PrivateVault;
  video: VideoPayload;
  title: string;
  onClose: () => void;
  onDelete?: () => void;
  /** React to the clip (either parent's); the player closes so its bubble can play the animation. */
  onReact?: (emoji: string) => void;
  mine?: ReadonlySet<string>;
}) {
  const { url: poster } = usePrivatePicture(token, video.poster, 'image/jpeg', vault);
  const { url, failed, progress } = usePrivateVideo(token, video, vault);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[90] flex flex-col bg-black" role="dialog" aria-modal="true" aria-label={title} data-testid="private-video-player" {...noSave}>
      <div className="flex shrink-0 items-center gap-2 px-3 pb-2 pt-[max(0.75rem,var(--safe-top))]">
        <p className="min-w-0 flex-1 truncate text-sm font-semibold text-white">{title}</p>
        {video.duration > 0 && <span className="shrink-0 text-xs tabular-nums text-slate-300">{formatDuration(video.duration)}</span>}
        <button type="button" onClick={onClose} aria-label="Close video" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/10 text-white">
          <X size={20} />
        </button>
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center px-2">
        {url ? (
          <video
            src={url}
            poster={poster || undefined}
            controls
            autoPlay
            playsInline
            controlsList="nodownload noremoteplayback"
            disablePictureInPicture
            disableRemotePlayback
            {...noSave}
            className="h-full w-full select-none object-contain"
            data-testid="private-video-playing"
          />
        ) : (
          <>
            {poster && <img src={poster} alt="" {...noSave} className="pointer-events-none max-h-full max-w-full select-none object-contain opacity-60" />}
            <p className="absolute inset-x-6 top-1/2 flex -translate-y-1/2 items-center justify-center gap-2 text-center text-sm text-white" role={failed ? 'alert' : 'status'} data-testid="private-video-status">
              {failed ? (
                failed
              ) : (
                <>
                  <Loader2 size={16} className="shrink-0 animate-spin" />
                  {progress.count > 1 ? `Decrypting ${progress.done} of ${progress.count}` : 'Decrypting'}
                </>
              )}
            </p>
          </>
        )}
      </div>
      {(onReact || onDelete) && (
        <div className="flex shrink-0 flex-col items-center gap-2 px-3 pt-2 pb-[max(0.75rem,var(--safe-bottom))]">
          {onReact && (
            <div className="w-full max-w-sm">
              <ReactionRow mine={mine ?? new Set()} onReact={onReact} tone="viewer" />
            </div>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={onDelete}
              data-testid="private-video-delete"
              className="flex min-h-11 items-center gap-1.5 rounded-full border border-rose-400/30 bg-rose-500/20 px-4 text-xs font-semibold text-rose-100"
            >
              <Trash2 size={13} aria-hidden /> Delete for both of you
            </button>
          )}
        </div>
      )}
      {!(onReact || onDelete) && <div className="shrink-0 pb-[max(0.75rem,var(--safe-bottom))]" />}
    </div>
  );
}
