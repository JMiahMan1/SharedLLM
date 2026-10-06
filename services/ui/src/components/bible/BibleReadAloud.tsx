import { useEffect, useRef, useState } from 'react';
import { Loader2, Pause, Play, Volume2 } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';

import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';

interface Props {
  /** The passage on screen. Read aloud follows what is being read. */
  reference: string;
  version: string;
  voice: string;
  onVoiceChange: (voice: string) => void;
}

type State = 'idle' | 'loading' | 'playing' | 'paused';

/**
 * Reading the passage aloud.
 *
 * The audio arrives as base64 in the response and is played through a `data:`
 * URL, so no audio file is written anywhere or served from another host. The
 * engine is ours, which means its failures are ours to explain: a missing
 * Kokoro voice file or a passage too long to narrate comes back as its own
 * sentence and is shown verbatim rather than becoming a silent no-op.
 */
export default function BibleReadAloud({ reference, version, voice, onVoiceChange }: Props) {
  const { trigger } = useHaptics();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef('');
  const [state, setState] = useState<State>('idle');
  const [message, setMessage] = useState('');
  const [cached, setCached] = useState(false);

  const voices = useQuery({
    queryKey: ['bible-voices'],
    queryFn: () => api.getBibleVoices(),
    retry: false,
    staleTime: 60 * 60 * 1000,
  });

  useEffect(() => {
    const element = audioRef.current;
    if (!element) return;
    const ended = () => setState('idle');
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

  async function play() {
    const element = audioRef.current;
    if (!element) return;
    if (state === 'paused') {
      await element.play();
      setState('playing');
      return;
    }
    setState('loading');
    setMessage('');
    try {
      const data = await api.getBibleNarration(reference, version, voice || undefined);
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      const blob = new Blob([toBytes(data.audio_base64)], { type: data.mime_type || 'audio/wav' });
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      element.src = url;
      setCached(data.cached);
      await element.play();
      setState('playing');
      void trigger('success');
    } catch (error) {
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
    void play();
  }

  function stop() {
    const element = audioRef.current;
    if (element) {
      element.pause();
      element.removeAttribute('src');
    }
    setState('idle');
  }

  const working = state === 'loading';
  const voicesError = voices.error instanceof Error ? voices.error.message : '';
  const options = voices.data?.voices ?? [];

  return (
    <div className="glass-panel rounded-2xl p-3 border border-white/5" data-testid="bible-read-aloud">
      <div className="flex items-center gap-2 flex-wrap">
        <Volume2 size={14} className="text-sky-300 shrink-0" />
        <h2 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Read aloud</h2>
        {state === 'playing' || state === 'paused' ? (
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
      ) : state === 'playing' && cached ? (
        <p className="mt-2 text-xs text-slate-500">Played from the narration cache.</p>
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