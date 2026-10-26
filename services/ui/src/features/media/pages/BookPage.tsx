import { useParams } from 'react-router-dom';
import { CheckCircle2, Gauge, Play, RotateCcw, RotateCw } from 'lucide-react';
import toast from 'react-hot-toast';
import { isAxiosError } from 'axios';
import { postAbsProgress } from '../api/client';
import { useBooksInProgress, useMediaItem } from '../api/queries';
import { formatDuration, formatTimeLeft } from '../format';
import { useLivePosition } from '../engine/useLivePosition';
import { useMediaStore } from '../store/mediaStore';
import { Artwork } from '../components/Artwork';
import BackToLibrary from '../components/BackToLibrary';
import { EmptyState, ErrorRetry, RowSkeletons } from '../components/StateViews';
import { TrackRow } from '../components/TrackRow';
import { iconButton, muted, primaryButton, secondaryButton, text } from '../components/ui';
import { useMediaScreen } from './MediaScreen';

const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2] as const;
export const SKIP_BACK = 15;
export const SKIP_FORWARD = 30;

/**
 * An audiobook (§4.2): cover, author, series, length, how far in, Resume,
 * the chapters (a tap plays from that chapter) and the description. While the
 * book is what the selected output plays, −15 s / +30 s sit here too, and on
 * this device the speed. "Mark finished" saves it as done in Audiobookshelf.
 * Its progress is saved back as it plays (`engine/absProgress.ts`).
 */
export default function BookPage() {
  const { id = '' } = useParams();
  const q = useMediaItem(`abs://${id}`);
  const books = useBooksInProgress();
  const { playback, player } = useMediaScreen();
  const target = useMediaStore((s) => s.activeOutputId);
  const remote = useMediaStore((s) => (target ? s.players[target] ?? null : null));
  const session = useMediaStore((s) => s.absSession);
  const remotePosition = useLivePosition(remote);

  if (q.isLoading) {
    return (
      <div className="space-y-4">
        <BackToLibrary to="/media/library/audiobooks" label="Audiobooks" />
        <RowSkeletons label="Loading book" />
      </div>
    );
  }
  if (q.isError || !q.data) {
    const notFound = isAxiosError(q.error) && q.error.response?.status === 404;
    return (
      <div className="space-y-4">
        <BackToLibrary to="/media/library/audiobooks" label="Audiobooks" />
        {notFound
          ? <EmptyState title="This book isn’t in your library" action={{ label: 'Go to your library', to: '/media/library/audiobooks' }} />
          : <ErrorRetry title="Couldn’t load this book" detail="Your audiobook server did not answer." onRetry={() => void q.refetch()} />}
      </div>
    );
  }

  const book = q.data;
  const duration = book.duration ?? 0;
  const inProgress = books.data?.books.find((b) => b.id === id);
  const raw = Number(inProgress?.progress ?? 0);
  const progress = inProgress?.is_complete ? 1 : raw > 1 ? raw / 100 : raw;
  const req = { kind: 'abs' as const, id, title: book.name, durationSec: duration || null };
  const busyKey = `play:${book.uri}`;
  // Is this book what the selected output is playing right now?
  const current = session?.itemId === id && session.outputId === target;
  const position = current ? (target ? remotePosition : player.position) : progress * duration;

  // Chapters run back to back: each starts where the ones before it end.
  const starts: number[] = [];
  book.children.reduce((at, c) => {
    starts.push(at);
    return at + (c.duration ?? 0);
  }, 0);

  const playFrom = async (seconds: number) => {
    // `play` swallows a failure into the error state; only seek when it
    // actually started, so a failed chapter does not jump whatever else is on.
    if (!current && !(await playback.play(req, busyKey))) return;
    await playback.seek(seconds);
  };
  const markFinished = async () => {
    if (!(duration > 0)) return;
    try {
      await postAbsProgress({ item_id: id, current_time: duration, duration, is_finished: true });
      toast.success(`${book.name} marked as finished`);
      void books.refetch();
    } catch {
      toast.error('Could not mark the book as finished');
    }
  };
  const cycleSpeed = () => {
    const i = SPEEDS.indexOf(player.playbackRate as (typeof SPEEDS)[number]);
    player.setPlaybackRate(SPEEDS[(i + 1) % SPEEDS.length] ?? 1);
  };

  return (
    <>
      <BackToLibrary to="/media/library/audiobooks" label="Audiobooks" />
      <header className="flex flex-col gap-5 sm:flex-row sm:items-end">
        <div className="w-44 shrink-0 sm:w-52">
          <Artwork image={book.image} kind="audiobook" size={208} fill />
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <p className={`text-xs font-medium uppercase tracking-wide ${muted}`}>Audiobook</p>
          <h1 className={`text-[clamp(1.5rem,4vw,2.25rem)] font-bold leading-tight ${text}`}>{book.name}</h1>
          <p className={`text-sm ${muted}`}>
            {[book.artist, book.album, duration ? formatDuration(duration) : ''].filter(Boolean).join(' · ')}
          </p>
          {progress > 0 ? (
            <div className="max-w-md space-y-1">
              <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-surface-2)]" role="progressbar" aria-label="Listened" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
                <div className="h-full rounded-full bg-[var(--site-accent)]" style={{ width: `${Math.round(progress * 100)}%` }} />
              </div>
              <p className={`text-xs ${muted}`}>
                {progress >= 1 ? 'Finished' : `${Math.round(progress * 100)}% listened${duration ? ` · ${formatTimeLeft(progress * duration, duration)}` : ''}`}
              </p>
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-2 pt-2">
            <button type="button" onClick={() => void playback.play(req, busyKey)} disabled={playback.busy === busyKey} className={primaryButton}>
              <Play size={18} aria-hidden />{progress > 0 && progress < 1 ? 'Resume' : 'Play'}
            </button>
            {current ? (
              <>
                <button type="button" onClick={() => void playback.seek(Math.max(0, position - SKIP_BACK))} aria-label={`Back ${SKIP_BACK} seconds`} className={iconButton}>
                  <RotateCcw size={20} aria-hidden />
                </button>
                <button type="button" onClick={() => void playback.seek(position + SKIP_FORWARD)} aria-label={`Forward ${SKIP_FORWARD} seconds`} className={iconButton}>
                  <RotateCw size={20} aria-hidden />
                </button>
                {!target ? (
                  <button type="button" onClick={cycleSpeed} aria-label={`Playback speed ${player.playbackRate}×`} className={secondaryButton}>
                    <Gauge size={16} aria-hidden />{player.playbackRate}×
                  </button>
                ) : null}
              </>
            ) : null}
            {progress < 1 && duration > 0 ? (
              <button type="button" onClick={() => void markFinished()} className={secondaryButton}>
                <CheckCircle2 size={16} aria-hidden />Mark finished
              </button>
            ) : null}
          </div>
        </div>
      </header>

      {book.description ? <p className={`max-w-3xl whitespace-pre-line text-sm ${muted}`}>{book.description}</p> : null}

      {book.children.length ? (
        <section aria-labelledby="chapters" className="space-y-2">
          <h2 id="chapters" className={`text-lg font-semibold ${text}`}>Chapters</h2>
          <ul aria-label="Chapters" className="space-y-1">
            {book.children.map((chapter, i) => (
              <TrackRow
                key={chapter.uri}
                item={chapter}
                index={i + 1}
                showArt={false}
                subtitle={`Starts at ${formatDuration(starts[i])}`}
                onPlay={() => void playFrom(starts[i])}
              />
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
