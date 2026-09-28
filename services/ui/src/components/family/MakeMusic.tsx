import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Music, Loader2, Play, Square } from 'lucide-react';
import { api } from '../../services/api';

const PRESETS = [
  { label: 'Calm piano', prompt: 'a calm piano piece, gentle and slow, for a quiet evening' },
  { label: 'Kids march', prompt: 'a cheerful marching song for children, clapping rhythm' },
  { label: 'Bedtime', prompt: 'a soft lullaby, warm strings and a music box, very sleepy' },
];

/**
 * Turn a phrase into a short piece of music and play it straight away.
 *
 * The audio backend is optional: when it is not configured the failure is
 * shown here, naming the setting, instead of a button that quietly does
 * nothing.
 */
export default function MakeMusic() {
  const [prompt, setPrompt] = useState('');
  const [duration, setDuration] = useState(8);
  const [audio, setAudio] = useState<{ url: string; mime: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);

  const generate = useMutation({
    mutationFn: () => api.generateMusic({ prompt: prompt.trim(), duration_s: duration }),
    onSuccess: (res) => {
      if (res.status === 'SUCCESS' && res.audio_url) {
        setAudio({ url: res.audio_url, mime: res.mime ?? 'audio/wav' });
        setError(null);
        setPlaying(true);
      } else {
        setError(res.message ?? 'The audio backend did not return a song');
      }
    },
    onError: (err: Error) => setError(err.message || 'Could not make that song'),
  });

  return (
    <div className="glass-panel p-4 rounded-2xl border border-white/5 space-y-3" data-testid="make-music">
      <div className="flex items-center gap-2">
        <Music size={16} className="text-emerald-300" />
        <span className="text-sm font-semibold text-slate-200">Make music</span>
      </div>

      <div className="flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            onClick={() => setPrompt(preset.prompt)}
            className="glass-button min-h-9 px-2.5 py-1.5 text-[11px]"
          >
            {preset.label}
          </button>
        ))}
      </div>

      <input
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        placeholder="Describe the song — 'a bouncy tune about a very slow turtle'"
        aria-label="Song prompt"
        className="glass-input w-full px-3 py-2 text-sm"
      />

      <label className="flex items-center gap-2 text-xs text-slate-400">
        Length
        <input
          type="range"
          min={2}
          max={30}
          value={duration}
          onChange={(event) => setDuration(Number(event.target.value))}
          aria-label="Song length"
          className="flex-1"
        />
        <span className="w-10 text-right">{duration}s</span>
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => generate.mutate()}
          disabled={generate.isPending}
          className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-60"
        >
          {generate.isPending ? <Loader2 size={14} className="animate-spin" /> : <Music size={14} />} Make the song
        </button>
        {audio && (
          <button
            type="button"
            onClick={() => setPlaying((p) => !p)}
            className="glass-button min-h-11 px-3 py-2 text-sm"
            aria-label={playing ? 'Stop playing' : 'Play the song'}
          >
            {playing ? <Square size={14} /> : <Play size={14} />}
          </button>
        )}
        {error && (
          <span className="text-xs text-rose-300" role="alert">
            {error}
          </span>
        )}
      </div>

      {audio && (
        <audio
          src={audio.url}
          controls
          data-testid="music-audio"
          className="w-full"
          onEnded={() => setPlaying(false)}
        />
      )}
    </div>
  );
}
