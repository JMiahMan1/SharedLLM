import { useState, type ReactNode } from 'react';
import { Globe, Link2, Loader2, Music, Play, X } from 'lucide-react';
import { hostOf, playerUrl, type LinkPreview } from '../../lib/privateLinkPreview';

/** Keeps a tap or a key inside the card from also opening the message's sheet. */
const stop = (event: { stopPropagation: () => void }) => event.stopPropagation();

function Words({ preview, compact = false }: { preview: LinkPreview; compact?: boolean }) {
  const site = preview.site_name || hostOf(preview.url);
  return (
    <span className={`block min-w-0 ${compact ? '' : 'px-3 py-2'}`}>
      {preview.title && <span className={`font-semibold leading-snug ${compact ? 'line-clamp-1 text-sm' : 'line-clamp-2 text-sm'}`}>{preview.title}</span>}
      {preview.description && !compact && <span className="mt-0.5 line-clamp-2 text-xs leading-snug opacity-80">{preview.description}</span>}
      {site && (
        <span className="mt-0.5 flex min-w-0 items-center gap-1 text-[11px] opacity-70">
          <Globe size={11} className="shrink-0" aria-hidden />
          <span className="truncate">{site}</span>
        </span>
      )}
    </span>
  );
}

/** The page itself, opened in the browser like a link in the text (LinkText). */
function OpenLink({ preview, children, testId }: { preview: LinkPreview; children: ReactNode; testId: string }) {
  return (
    <a href={preview.url} target="_blank" rel="noopener noreferrer" onClick={stop} className="block min-h-11 no-underline" data-testid={testId}>
      {children}
    </a>
  );
}

/**
 * A received (or sent) message's link preview, drawn only from what was in the
 * sealed message. A video or audio player is not on the page until it is
 * tapped, so the site that hosts it hears nothing before then.
 */
export default function PrivateLinkCard({ preview, own }: { preview: LinkPreview; own: boolean }) {
  const [playing, setPlaying] = useState(false);
  const label = preview.title || hostOf(preview.url) || 'this';
  const shell = `mt-2 block w-64 max-w-full overflow-hidden whitespace-normal rounded-2xl text-left ${
    own ? 'bg-black/20' : 'border border-white/10 bg-black/25'
  }`;

  if (preview.kind === 'video' && preview.embed) {
    const embed = preview.embed;
    return (
      <span className={shell} onClick={stop} onKeyDown={stop} data-testid="private-link-card" data-kind="video">
        {playing ? (
          embed.provider === 'file' ? (
            <video
              src={embed.src}
              controls
              autoPlay
              playsInline
              preload="none"
              className="block aspect-video w-full bg-black"
              data-testid="private-link-player"
            />
          ) : (
            <iframe
              src={playerUrl(embed)}
              title={label}
              className="block aspect-video w-full border-0 bg-black"
              allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
              allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin"
              sandbox="allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox"
              data-testid="private-link-player"
            />
          )
        ) : (
          <button
            type="button"
            onClick={() => setPlaying(true)}
            aria-label={`Play video: ${label}`}
            data-testid="private-link-play"
            className="relative block aspect-video w-full overflow-hidden bg-gradient-to-br from-slate-700 to-slate-900"
          >
            {preview.image && <img src={preview.image} alt="" draggable={false} className="h-full w-full object-cover" />}
            <span className="absolute inset-0 flex items-center justify-center">
              <span className="flex h-14 w-14 items-center justify-center rounded-full bg-black/60 text-white shadow-lg ring-1 ring-white/30">
                <Play size={24} className="ml-1" fill="currentColor" aria-hidden />
              </span>
            </span>
          </button>
        )}
        <OpenLink preview={preview} testId="private-link-open">
          <Words preview={preview} />
        </OpenLink>
      </span>
    );
  }

  if (preview.kind === 'audio' && preview.embed?.provider === 'file') {
    const embed = preview.embed;
    return (
      <span className={shell} onClick={stop} onKeyDown={stop} data-testid="private-link-card" data-kind="audio">
        <OpenLink preview={preview} testId="private-link-open">
          <span className="flex items-center">
            {preview.image ? (
              <img src={preview.image} alt="" draggable={false} className="ml-3 h-12 w-12 shrink-0 rounded-lg object-cover" />
            ) : (
              <span className="ml-3 flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-white/10">
                <Music size={20} aria-hidden />
              </span>
            )}
            <Words preview={preview} />
          </span>
        </OpenLink>
        <span className="block px-2 pb-2">
          {playing ? (
            // Mounted only now, so nothing is asked of the file's site before the tap.
            <audio src={embed.src} controls autoPlay preload="none" className="block h-11 w-full" data-testid="private-link-player" />
          ) : (
            <button
              type="button"
              onClick={() => setPlaying(true)}
              aria-label={`Play audio: ${label}`}
              data-testid="private-link-play"
              className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-white/10 text-sm font-semibold hover:bg-white/15"
            >
              <Play size={15} fill="currentColor" aria-hidden /> Play
            </button>
          )}
        </span>
      </span>
    );
  }

  return (
    <span className={shell} onClick={stop} onKeyDown={stop} data-kind="link">
      <OpenLink preview={preview} testId="private-link-card">
        {preview.image && <img src={preview.image} alt="" draggable={false} className="block max-h-40 w-full object-cover" />}
        <Words preview={preview} />
      </OpenLink>
    </span>
  );
}

export type DraftPreview =
  | { status: 'loading'; url: string }
  | { status: 'ready'; url: string; preview: LinkPreview };

/** The preview about to be sent, above the composer: loading, then the card, with an X to send without it. */
export function PrivateLinkDraft({ draft, onDismiss }: { draft: DraftPreview; onDismiss: () => void }) {
  const preview = draft.status === 'ready' ? draft.preview : null;
  return (
    <div className="flex shrink-0 items-center gap-2 border-t border-white/5 bg-slate-900/95 py-1 pl-3 pr-1 text-slate-100" data-testid="private-link-draft">
      {draft.status === 'loading' ? (
        <span className="flex min-w-0 flex-1 items-center gap-2 text-xs text-slate-300" role="status" data-testid="private-link-draft-loading">
          <Loader2 size={14} className="shrink-0 animate-spin" aria-hidden />
          <span className="truncate">Making a preview of {hostOf(draft.url) || 'the link'}</span>
        </span>
      ) : preview ? (
        <span className="flex min-w-0 flex-1 items-center gap-2">
          {preview.image ? (
            <img src={preview.image} alt="" draggable={false} className="h-11 w-11 shrink-0 rounded-lg object-cover" />
          ) : (
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-white/10">
              {preview.kind === 'audio' ? <Music size={18} aria-hidden /> : preview.kind === 'video' ? <Play size={18} aria-hidden /> : <Link2 size={18} aria-hidden />}
            </span>
          )}
          <span className="min-w-0 flex-1" data-testid="private-link-draft-card">
            <Words preview={preview} compact />
          </span>
        </span>
      ) : null}
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Send without a preview"
        data-testid="private-link-draft-remove"
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-300 hover:bg-white/10"
      >
        <X size={17} aria-hidden />
      </button>
    </div>
  );
}
