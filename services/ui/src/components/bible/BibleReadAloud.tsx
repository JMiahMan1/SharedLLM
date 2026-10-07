import { useEffect, useRef, useState } from 'react';
import { Loader2, Pause, Play, Volume2 } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';

import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { BibleNarrationPlan } from '../../types/api';

interface Props {
  /** The passage on screen. Read aloud follows what is being read. */
  reference: string;
  version: string;
  voice: string;
  onVoiceChange: (voice: string) => void;
}

type State = 'idle' | 'loading' | 'playing' | 'paused';

interface SpokenPiece {
  bytes: Uint8Array;
  mime: string;
}

/**
 * Spoken pieces already fetched in this session, keyed the way the server keys
 * them. A piece is a fraction of a chapter, so replaying a passage -- or
 * playing a verse whose chapter was just read aloud -- asks the network for
 * nothing at all. The map is dropped wholesale when it grows past a limit
 * rather than tracked per entry: it is a cache, not a database.
 */
const spoken = new Map<string, SpokenPiece>();
const SPOKEN_LIMIT = 120;

function remember(key: string, piece: SpokenPiece) {
  if (spoken.size >= SPOKEN_LIMIT) spoken.clear();
  spoken.set(key, piece);
}

/**
 * Reading the passage aloud.
 *
 * The passage is spoken as a sequence of pieces. The first is short, so sound
 * starts while the rest is still being rendered on the server, and the rest are
 * fetched one ahead while the reader is listening -- a chapter that takes
 * twenty seconds to render used to be twenty seconds of silence, and is now
 * about two. Every piece arrives as base64 and is played through a blob URL, so
 * no audio file is written anywhere or served from another host.
 *
 * The engine is ours, which means its failures are ours to explain: a missing
 * Kokoro voice file or a passage too long to narrate comes back as its own
 * sentence and is shown verbatim rather than becoming a silent no-op.
 */
export default function BibleReadAloud({ reference, version, voice, onVoiceChange }: Props) {
  const { trigger } = useHaptics();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef('');
  const planRef = useRef<BibleNarrationPlan | null>(null);
  const nextRef = useRef(1);
  const runRef = useRef(0);
  const sourceRef = useRef({ reference, version, voice });
  const advanceRef = useRef<() => Promise<void>>(async () => {});
  const [state, setState] = useState<State>('idle');
  const [message, setMessage] = useState('');
  const [cachedAll, setCachedAll] = useState(false);
  const [progress, setProgress] = useState({ played: 0, total: 0 });

  const voices = useQuery({
    queryKey: ['bible-voices'],
    queryFn: () => api.getBibleVoices(),
    retry: false,
    staleTime: 60 * 60 * 1000,
  });

  useEffect(() => {
    sourceRef.current = { reference, version, voice };
  }, [reference, version, voice]);

  useEffect(() => {
    const element = audioRef.current;
    if (!element) return;
    const ended = () => {
      void advanceRef.current();
    };
    const failed = () => {
      setState('idle');
      setMessage('Playback failed. The audio was produced but this device would not play it.');
    };
    element.addEventListener('ended', ended);
    element.addEventListener('error', failed);
    return () => {
      element.removeEventListener('ended', ended);
      element.removeEventListener('error', failed);
    };
  }, []);

  useEffect(() => {
    return () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);

  /** The piece's audio, from this session's cache when the server says it can be. */
  async function pieceFor(index: number): Promise<SpokenPiece> {
    const { reference: ref, version: ver, voice: chosen } = sourceRef.current;
    const key = `${ver}|${ref}|${chosen}|${index}`;
    // Only reuse this session's copy when the server reports the piece cached:
    // a re-imported translation has the same address and different words, and
    // the server's answer is what knows the difference.
    if (planRef.current?.chunks[index]?.cached) {
      const hit = spoken.get(key);
      if (hit) return hit;
    }
    const data = await api.getBibleNarrationChunk(ref, index, ver, chosen || undefined);
    const piece: SpokenPiece = {
      bytes: toBytes(data.audio_base64),
      mime: data.mime_type || 'audio/wav',
    };
    remember(key, piece);
    return piece;
  }

  async function playPiece(piece: SpokenPiece) {
    const element = audioRef.current;
    if (!element) return;
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    const url = URL.createObjectURL(new Blob([piece.bytes], { type: piece.mime }));
    urlRef.current = url;
    element.src = url;
    await element.play();
  }

  /** Fetch the pieces after the one playing, so the next is ready before it is needed. */
  async function prefetch(run: number, count: number) {
    for (let index = nextRef.current; index < count; index += 1) {
      if (runRef.current !== run) return;
      try {
        await pieceFor(index);
      } catch {
        // The failure will be reported if the reader actually reaches this
        // piece; abandoning the prefetch early keeps the message about the
        // piece they hear rather than one they never did.
        return;
      }
    }
  }

  async function advance() {
    const plan = planRef.current;
    if (!plan) {
      setState('idle');
      return;
    }
    if (nextRef.current >= plan.count) {
      setState('idle');
      return;
    }
    const index = nextRef.current;
    nextRef.current = index + 1;
    try {
      const piece = await pieceFor(index);
      await playPiece(piece);
      setProgress({ played: nextRef.current, total: plan.count });
    } catch (error) {
      setState('idle');
      setMessage(messageOf(error));
    }
  }

  // The ended-event listener is registered once, so it reaches the current
  // advance through a ref rather than a stale closure. Assigned in an effect
  // because a ref must not be written during render.
  useEffect(() => {
    advanceRef.current = advance;
  });

  async function start() {
    const element = audioRef.current;
    if (!element) return;
    setState('loading');
    setMessage('');
    const run = runRef.current + 1;
    runRef.current = run;
    try {
      const { reference: ref, version: ver, voice: chosen } = sourceRef.current;
      const plan = await api.getBibleNarrationPlan(ref, ver, chosen || undefined);
      if (runRef.current !== run) return;
      planRef.current = plan;
      setCachedAll(plan.all_cached);
      setProgress({ played: 1, total: plan.count });
      nextRef.current = 1;
      const first = await pieceFor(0);
      if (runRef.current !== run) return;
      await playPiece(first);
      setState('playing');
      void trigger('success');
      void prefetch(run, plan.count);
    } catch (error) {
      if (runRef.current !== run) return;
      setState('idle');
      setMessage(messageOf(error));
    }
  }

  function toggle() {
    const element = audioRef.current;
    if (!element) return;
    if (state === 'playing') {
      element.pause();
      setState('paused');
      return;
    }
    if (state === 'paused') {
      void element.play();
      setState('playing');
      return;
    }
    void start();
  }

  function stop() {
    runRef.current += 1;
    const element = audioRef.current;
    if (element) {
      element.pause();
      element.removeAttribute('src');
    }
    planRef.current = null;
    setState('idle');
    setProgress({ played: 0, total: 0 });
  }

  const working = state === 'loading';
  const voicesError = voices.error instanceof Error ? voices.error.message : '';
  const options = voices.data?.voices ?? [];
  const listening = state === 'playing' || state === 'paused';

  return (
    <div className="glass-panel rounded-2xl p-3 border border-white/5" data-testid="bible-read-aloud">
      <div className="flex items-center gap-2 flex-wrap">
        <Volume2 size={14} className="text-sky-300 shrink-0" />
        <h2 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Read aloud</h2>
        {listening ? (
          <button
            type="button"
            onClick={stop}
            className="ml-auto min-h-11 px-3 rounded-xl text-xs text-slate-400 hover:text-white"
          >
            Stop
          </button>
        ) : null}
      </div>

      <div className="mt-2 flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={toggle}
          disabled={working}
          data-testid="bible-read-aloud-play"
          className="min-h-11 px-4 rounded-xl text-sm font-medium bg-sky-500/20 text-sky-200 hover:bg-sky-500/30 disabled:opacity-60 flex items-center gap-2"
        >
          {working ? <Loader2 size={15} className="animate-spin" /> : state === 'playing' ? <Pause size={15} /> : <Play size={15} />}
          {working ? 'Preparing…' : state === 'paused' ? 'Resume' : state === 'playing' ? 'Pause' : 'Read this passage'}
        </button>

        {options.length > 1 ? (
          <>
            <label htmlFor="bible-read-aloud-voice" className="sr-only">
                Narration voice
              </label>
            <select
              id="bible-read-aloud-voice"
              data-testid="bible-read-aloud-voice"
              value={voice}
              onChange={(event) => onVoiceChange(event.target.value)}
              className="min-h-11 rounded-xl bg-slate-800/80 border border-white/10 px-3 text-xs text-slate-200"
            >
              <option value="">Engine default</option>
              {options.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </>
        ) : null}
      </div>

      <audio ref={audioRef} preload="none" className="hidden" data-testid="bible-read-aloud-audio" />

      {message ? (
        <p className="mt-2 text-xs text-amber-300/90" data-testid="bible-read-aloud-message">
          {message}
        </p>
      ) : voicesError ? (
        <p className="mt-2 text-xs text-amber-300/90" data-testid="bible-read-aloud-voices-error">
          {voicesError}
        </p>
      ) : listening ? (
        <p className="mt-2 text-xs text-slate-500" data-testid="bible-read-aloud-progress">
          {progress.total > 1 ? `Part ${Math.min(progress.played, progress.total)} of ${progress.total}` : 'Playing'}
          {cachedAll ? ' · from the narration cache' : ''}
        </p>
      ) : null}
    </div>
  );
}

function toBytes(base64: string): Uint8Array<ArrayBuffer> {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  return 'Read aloud is unavailable.';
}
