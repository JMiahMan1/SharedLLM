import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Capacitor } from '@capacitor/core';
import { Loader2, Pause, Play, SkipForward, Square, X } from 'lucide-react';

import { api } from '../../services/api';
import { base64ToBytes } from '../../lib/base64';
import { claimSpeechSession, releaseSpeechSession } from '../../lib/speechSession';
import JarvisMedia from '../../plugins/jarvisMedia';
import { applySessionMetadata, bindSessionHandlers } from '../../features/media/engine/mediaSession';
import type { StudyNarrationMark, StudyNarrationPlan } from '../../types/api';
import { blockAt, startingPiece } from './studyNarration';

/**
 * Reading an imported study section aloud, the way Scripture is read aloud.
 *
 * The same engine and voices as the Bible reader (the server builds the
 * script from the stored section, skipping footnotes, tables and images, and
 * announcing discussion questions), the same piece-at-a-time playback (the
 * first piece is short, the next is fetched while this one plays), plus:
 *
 * - it starts at the paragraph on screen, not the top of the section,
 * - the paragraph being spoken is reported (`onBlock`) so the page can light it
 *   up and keep it in view,
 * - at the end of a section it can carry on into the next one,
 * - on Android it holds the media notification (as speech, so swiping the app
 *   away never starts the music player) and keeps playing with the screen off;
 *   lock-screen and headset play/pause reach it,
 * - stopping, closing the book or leaving the page stops the sound.
 */
interface Props {
  bookId: number;
  ordinal: number;
  bookTitle: string;
  sectionLabel: string;
  voice: string;
  onVoiceChange: (voice: string) => void;
  /** The block the reader is looking at, where reading should start. */
  startBlock: () => string | null;
  onBlock: (blockId: string | null) => void;
  /** The section has been read to its end. */
  onFinished: () => void;
  onClose: () => void;
  /** Bumped by the reader to start playing at once (a new section while continuing). */
  autoStart?: number;
  continueOn: boolean;
  onContinueChange: (value: boolean) => void;
}

type State = 'idle' | 'loading' | 'playing' | 'paused';

const RATES = [0.75, 1, 1.25, 1.5] as const;
const RATE_KEY = 'jarvis.study-read-rate';

function savedRate(): number {
  try {
    const value = Number(window.localStorage.getItem(RATE_KEY));
    return RATES.includes(value as (typeof RATES)[number]) ? value : 1;
  } catch {
    return 1;
  }
}

export default function StudyReadAloud({
  bookId,
  ordinal,
  bookTitle,
  sectionLabel,
  voice,
  onVoiceChange,
  startBlock,
  onBlock,
  onFinished,
  onClose,
  autoStart = 0,
  continueOn,
  onContinueChange,
}: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef('');
  const planRef = useRef<StudyNarrationPlan | null>(null);
  const indexRef = useRef(0);
  const runRef = useRef(0);
  const marksRef = useRef<StudyNarrationMark[]>([]);
  const cacheRef = useRef(new Map<number, { bytes: Uint8Array; mime: string; blocks: StudyNarrationMark[] }>());
  const sessionId = useRef(`study-${useId()}`);
  const native = Capacitor.isNativePlatform();
  const [state, setState] = useState<State>('idle');
  const [message, setMessage] = useState('');
  const [progress, setProgress] = useState({ part: 0, total: 0 });
  const [rate, setRate] = useState<number>(savedRate);
  const callbacks = useRef({ onBlock, onFinished, startBlock });
  useEffect(() => {
    callbacks.current = { onBlock, onFinished, startBlock };
  });

  const voices = useQuery({
    queryKey: ['bible-voices'],
    queryFn: () => api.getBibleVoices(),
    retry: false,
    staleTime: 60 * 60 * 1000,
  });

  const releaseSession = useCallback(() => {
    releaseSpeechSession(sessionId.current);
    applySessionMetadata(null);
    if (native) void JarvisMedia.stop().catch(() => {});
  }, [native]);

  const halt = useCallback(() => {
    runRef.current += 1;
    const element = audioRef.current;
    if (element) {
      element.pause();
      element.removeAttribute('src');
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = '';
    }
    planRef.current = null;
    marksRef.current = [];
    setState('idle');
    setProgress({ part: 0, total: 0 });
    callbacks.current.onBlock(null);
    releaseSession();
  }, [releaseSession]);

  async function piece(index: number) {
    const hit = cacheRef.current.get(index);
    if (hit) return hit;
    const data = await api.getStudyNarrationChunk(bookId, ordinal, index, voice || undefined);
    const entry = { bytes: base64ToBytes(data.audio_base64), mime: data.mime_type || 'audio/wav', blocks: data.blocks };
    cacheRef.current.set(index, entry);
    return entry;
  }

  async function playIndex(index: number, run: number) {
    const plan = planRef.current;
    const element = audioRef.current;
    if (!plan || !element) return;
    const entry = await piece(index);
    if (runRef.current !== run) return;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    const url = URL.createObjectURL(new Blob([entry.bytes as Uint8Array<ArrayBuffer>], { type: entry.mime }));
    urlRef.current = url;
    element.src = url;
    element.playbackRate = rate;
    indexRef.current = index;
    marksRef.current = entry.blocks.length ? entry.blocks : plan.chunks[index]?.blocks ?? [];
    callbacks.current.onBlock(marksRef.current[0]?.block_id ?? null);
    setProgress({ part: index + 1, total: plan.count });
    await element.play();
    setState('playing');
    // The next piece is fetched while this one plays.
    if (index + 1 < plan.count) void piece(index + 1).catch(() => undefined);
  }

  async function start(fromTop = false) {
    const run = runRef.current + 1;
    runRef.current = run;
    setState('loading');
    setMessage('');
    try {
      const plan = await api.getStudyNarrationPlan(bookId, ordinal, voice || undefined);
      if (runRef.current !== run) return;
      planRef.current = plan;
      claimSpeechSession(sessionId.current);
      await playIndex(fromTop ? 0 : startingPiece(plan, callbacks.current.startBlock()), run);
    } catch (error) {
      if (runRef.current !== run) return;
      setState('idle');
      setMessage(error instanceof Error ? error.message : 'Read aloud is unavailable.');
      releaseSession();
    }
  }

  const advance = useCallback(async () => {
    const plan = planRef.current;
    if (!plan) return;
    const next = indexRef.current + 1;
    if (next >= plan.count) {
      const run = runRef.current;
      halt();
      // A new run means the reader closed or restarted meanwhile.
      if (runRef.current === run + 1) callbacks.current.onFinished();
      return;
    }
    try {
      await playIndex(next, runRef.current);
    } catch (error) {
      setState('idle');
      setMessage(error instanceof Error ? error.message : 'Read aloud stopped.');
      callbacks.current.onBlock(null);
      releaseSession();
    }
    // playIndex reads state through refs; this callback only needs to be current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [halt, releaseSession]);

  const advanceRef = useRef(advance);
  useEffect(() => {
    advanceRef.current = advance;
  });

  function toggle() {
    const element = audioRef.current;
    if (!element) return;
    if (state === 'playing') {
      element.pause();
      setState('paused');
    } else if (state === 'paused') {
      void element.play().then(() => setState('playing')).catch(() => undefined);
    } else if (state === 'idle') {
      void start();
    }
  }
  const toggleRef = useRef(toggle);
  useEffect(() => {
    toggleRef.current = toggle;
  });

  // The audio element's own events, registered once and read through refs.
  useEffect(() => {
    const element = audioRef.current;
    if (!element) return undefined;
    const ended = () => void advanceRef.current();
    const timeupdate = () => {
      if (!element.duration || !Number.isFinite(element.duration)) return;
      const block = blockAt(marksRef.current, element.currentTime / element.duration);
      if (block) callbacks.current.onBlock(block);
    };
    const failed = () => {
      if (!element.getAttribute('src')) return;
      setState('idle');
      setMessage('Playback failed. The audio was produced but this device would not play it.');
    };
    element.addEventListener('ended', ended);
    element.addEventListener('timeupdate', timeupdate);
    element.addEventListener('error', failed);
    return () => {
      element.removeEventListener('ended', ended);
      element.removeEventListener('timeupdate', timeupdate);
      element.removeEventListener('error', failed);
    };
  }, []);

  // A new section or voice: pieces from the old one must not play on.
  const sourceKey = `${bookId}|${ordinal}|${voice}`;
  const sourceRef = useRef(sourceKey);
  useEffect(() => {
    if (sourceRef.current !== sourceKey) {
      sourceRef.current = sourceKey;
      cacheRef.current = new Map();
      halt();
    }
  }, [sourceKey, halt]);

  // Continue into the next section: the reader bumps autoStart after turning the page.
  const startedFor = useRef(0);
  useEffect(() => {
    if (autoStart && autoStart !== startedFor.current) {
      startedFor.current = autoStart;
      void start(true);
    }
    // start reads the current section through props at call time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, sourceKey]);

  // Lock screen, notification and headset controls.
  useEffect(() => {
    const listening = state === 'playing' || state === 'paused';
    if (!listening) return undefined;
    applySessionMetadata({
      title: sectionLabel,
      artist: bookTitle,
      image: null,
      isPlaying: state === 'playing',
      position: 0,
      duration: 0,
      playbackRate: rate,
    });
    if (native) {
      void JarvisMedia.update({ title: sectionLabel, artist: bookTitle, playing: state === 'playing', speech: true }).catch(
        () => {},
      );
    }
    const unbind = bindSessionHandlers({
      play: () => toggleRef.current(),
      pause: () => toggleRef.current(),
      stop: () => halt(),
      next: () => void advanceRef.current(),
      previous: () => undefined,
      seekTo: () => undefined,
      position: () => audioRef.current?.currentTime ?? 0,
    });
    return unbind;
  }, [state, sectionLabel, bookTitle, rate, native, halt]);

  useEffect(() => {
    if (!native) return undefined;
    const handle = JarvisMedia.addListener('action', ({ action }) => {
      if (!planRef.current) return;
      if (action === 'play' || action === 'pause') toggleRef.current();
      else if (action === 'next') void advanceRef.current();
    });
    return () => {
      void handle.then((h) => h.remove()).catch(() => {});
    };
  }, [native]);

  // Leaving the reader stops the voice: a detached <audio> keeps playing.
  useEffect(() => {
    const element = audioRef.current;
    const id = sessionId.current;
    return () => {
      runRef.current += 1;
      element?.pause();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      releaseSpeechSession(id);
      applySessionMetadata(null);
      if (Capacitor.isNativePlatform()) void JarvisMedia.stop().catch(() => {});
    };
  }, []);

  const working = state === 'loading';
  const options = voices.data?.voices ?? [];
  const listening = state === 'playing' || state === 'paused';

  return (
    <div
      className="border-t border-[var(--page-rule)] bg-[var(--page-paper)] px-2 py-1.5"
      data-testid="study-read-aloud"
      role="region"
      aria-label="Read aloud"
    >
      <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-1.5">
        <button
          type="button"
          onClick={toggle}
          disabled={working}
          aria-label={state === 'playing' ? 'Pause reading' : state === 'paused' ? 'Resume reading' : 'Read aloud from here'}
          data-testid="study-read-aloud-play"
          className="flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-xl bg-amber-500/20 px-3 text-sm font-medium text-[var(--page-ink)] hover:bg-amber-500/30 disabled:opacity-60"
        >
          {working ? <Loader2 size={16} className="animate-spin" /> : state === 'playing' ? <Pause size={16} /> : <Play size={16} />}
          <span className="hidden min-[380px]:inline">
            {working ? 'Preparing…' : state === 'playing' ? 'Pause' : state === 'paused' ? 'Resume' : 'Listen'}
          </span>
        </button>
        {listening ? (
          <>
            <button
              type="button"
              onClick={() => void advanceRef.current()}
              aria-label="Skip ahead"
              className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-[var(--page-muted)] hover:bg-[var(--page-hover)]"
            >
              <SkipForward size={16} />
            </button>
            <button
              type="button"
              onClick={halt}
              aria-label="Stop reading"
              data-testid="study-read-aloud-stop"
              className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-[var(--page-muted)] hover:bg-[var(--page-hover)]"
            >
              <Square size={15} />
            </button>
          </>
        ) : null}

        <label htmlFor="study-read-aloud-rate" className="sr-only">
          Reading speed
        </label>
        <select
          id="study-read-aloud-rate"
          data-testid="study-read-aloud-rate"
          value={rate}
          onChange={(event) => {
            const next = Number(event.target.value);
            setRate(next);
            if (audioRef.current) audioRef.current.playbackRate = next;
            try {
              window.localStorage.setItem(RATE_KEY, String(next));
            } catch {
              /* storage disabled: the speed still applies now */
            }
          }}
          className="min-h-11 rounded-xl border border-[var(--page-rule)] bg-transparent px-2 text-xs text-[var(--page-ink)]"
        >
          {RATES.map((value) => (
            <option key={value} value={value}>
              {value}x
            </option>
          ))}
        </select>

        {options.length > 1 ? (
          <>
            <label htmlFor="study-read-aloud-voice" className="sr-only">
              Voice
            </label>
            <select
              id="study-read-aloud-voice"
              data-testid="study-read-aloud-voice"
              value={voice}
              onChange={(event) => onVoiceChange(event.target.value)}
              className="min-h-11 min-w-0 max-w-[9rem] rounded-xl border border-[var(--page-rule)] bg-transparent px-2 text-xs text-[var(--page-ink)]"
            >
              <option value="">Default voice</option>
              {options.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </>
        ) : null}

        <button
          type="button"
          onClick={() => onContinueChange(!continueOn)}
          aria-pressed={continueOn}
          title="Carry on into the next section"
          data-testid="study-read-aloud-continue"
          className={`min-h-11 rounded-xl border px-2.5 text-xs ${
            continueOn
              ? 'border-amber-500/60 text-[var(--page-ink)]'
              : 'border-[var(--page-rule)] text-[var(--page-muted)]'
          }`}
        >
          Keep going
        </button>

        <button
          type="button"
          onClick={() => {
            halt();
            onClose();
          }}
          aria-label="Close read aloud"
          className="ml-auto flex min-h-11 min-w-11 items-center justify-center rounded-xl text-[var(--page-muted)] hover:bg-[var(--page-hover)]"
        >
          <X size={16} />
        </button>
      </div>
      {message ? (
        <p className="mx-auto mt-1 max-w-4xl px-1 text-xs text-amber-600" data-testid="study-read-aloud-message" role="status">
          {message}
        </p>
      ) : listening && progress.total > 1 ? (
        <p className="mx-auto mt-0.5 max-w-4xl px-1 text-[11px] text-[var(--page-muted)]" data-testid="study-read-aloud-progress">
          Part {progress.part} of {progress.total}
        </p>
      ) : null}
      <audio ref={audioRef} preload="none" className="hidden" data-testid="study-read-aloud-audio" />
    </div>
  );
}
