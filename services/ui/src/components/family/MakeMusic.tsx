import { useEffect, useRef, useState } from 'react';
import { Music, Loader2, X } from 'lucide-react';
import { api } from '../../services/api';
import type { SongJob } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { STRUCTURES, minutes, songStatus, type StructureId } from './songShapes';

const PRESETS = [
  { label: 'Calm piano', prompt: 'a calm piano piece, gentle and slow, for a quiet evening' },
  { label: 'Kids march', prompt: 'a cheerful marching song for children, clapping rhythm' },
  { label: 'Bedtime', prompt: 'a soft lullaby, warm strings and a music box, very sleepy' },
  { label: 'Worship', prompt: 'uplifting acoustic worship song, warm guitar and piano, hopeful' },
];

const POLL_MS = 2500;
/** Failed checks in a row before giving up on a song (about a minute). */
const MAX_MISSES = 24;
/** The song being written, so leaving the page and coming back picks it up. */
const JOB_KEY = 'make-music-job';

function rememberJob(id: string | null) {
  try {
    if (id) localStorage.setItem(JOB_KEY, id);
    else localStorage.removeItem(JOB_KEY);
  } catch {
    // Storage blocked: the song still finishes, it just is not resumed.
  }
}

function rememberedJob(): string | null {
  try {
    return localStorage.getItem(JOB_KEY);
  } catch {
    return null;
  }
}

/**
 * Describe a song and the audio server writes it, section by section, in a
 * queue shared with everyone's songs. It can take a few minutes (longer when
 * Jarvis is using the graphics card and the song is written on the CPU), so
 * progress is shown and the song is picked up again after leaving the page.
 */
export default function MakeMusic() {
  const [prompt, setPrompt] = useState('');
  const [structure, setStructure] = useState<StructureId>('song');
  const [duration, setDuration] = useState<number>(STRUCTURES[0].initial);
  const [job, setJob] = useState<SongJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // The song being followed; picked up from storage after leaving the page.
  const [following, setFollowing] = useState<string | null>(rememberedJob);
  const [tick, setTick] = useState(0);
  const misses = useRef(0);
  const shape = STRUCTURES.find((s) => s.id === structure) ?? STRUCTURES[0];

  useEffect(() => {
    if (!following) return;
    // A resumed song is checked at once; after that, every POLL_MS.
    const timer = setTimeout(
      async () => {
        try {
          const next = await api.getSong(following);
          misses.current = 0;
          setJob(next);
          if (next.status === 'queued' || next.status === 'running') {
            setTick((t) => t + 1);
            return;
          }
          if (next.status !== 'done') rememberJob(null);
          setFollowing(null);
        } catch (e) {
          // The audio server or gateway restarting is not the song failing:
          // keep following, unless the server says the job no longer exists.
          const gone = (e as { response?: { status?: number } })?.response?.status === 404;
          if (!gone && misses.current < MAX_MISSES) {
            misses.current += 1;
            setTick((t) => t + 1);
            return;
          }
          rememberJob(null);
          setFollowing(null);
          setError(errorText(e, 'Lost track of the song'));
        }
      },
      tick === 0 && !job ? 0 : POLL_MS,
    );
    return () => clearTimeout(timer);
    // `job` only decides the first delay; following it would restart the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [following, tick]);

  const chooseStructure = (id: StructureId) => {
    const next = STRUCTURES.find((s) => s.id === id) ?? STRUCTURES[0];
    setStructure(id);
    setDuration(next.initial);
  };

  const start = async () => {
    setError(null);
    setJob(null);
    setStarting(true);
    try {
      const started = await api.startSong({
        prompt: prompt.trim(),
        duration_s: duration,
        // A clip is one verse-length section: no intro, no chorus.
        structure: structure === 'clip' ? 'loop' : structure,
      });
      setJob(started);
      rememberJob(started.id);
      setTick(1);
      setFollowing(started.id);
    } catch (e) {
      setError(errorText(e, 'Could not start the song'));
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    setFollowing(null);
    try {
      setJob(await api.cancelSong(job.id));
      rememberJob(null);
    } catch (e) {
      setError(errorText(e, 'Could not cancel'));
      setFollowing(job.id);
    }
  };

  const working = starting || job?.status === 'queued' || job?.status === 'running';

  return (
    <div className="glass-panel p-4 rounded-2xl border border-white/5 space-y-3" data-testid="make-music">
      <div className="flex items-center gap-2">
        <Music size={16} className="text-emerald-300" />
        <span className="text-sm font-semibold text-slate-200">Make music</span>
      </div>

      <div className="flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <button key={preset.label} type="button" onClick={() => setPrompt(preset.prompt)} className="glass-button min-h-9 px-2.5 py-1.5 text-[11px]">
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

      <div role="radiogroup" aria-label="Song shape" className="flex flex-wrap gap-1.5">
        {STRUCTURES.map((s) => (
          <button
            key={s.id}
            type="button"
            role="radio"
            aria-checked={structure === s.id}
            onClick={() => chooseStructure(s.id)}
            disabled={working}
            className={`min-h-9 rounded-full px-3 text-xs border transition disabled:opacity-60 ${
              structure === s.id ? 'bg-emerald-500/20 border-emerald-400/50 text-emerald-100' : 'border-white/10 text-slate-300'
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-slate-500" data-testid="song-shape">
        {shape.detail}
      </p>

      <label className="flex items-center gap-2 text-xs text-slate-400">
        Length
        <input
          type="range"
          min={shape.min}
          max={shape.max}
          step={shape.step}
          value={duration}
          onChange={(event) => setDuration(Number(event.target.value))}
          aria-label="Song length"
          disabled={working}
          className="flex-1"
        />
        <span className="w-12 text-right">{minutes(duration)}</span>
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={start}
          disabled={working || !prompt.trim()}
          className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-60"
        >
          {working ? <Loader2 size={14} className="animate-spin" /> : <Music size={14} />} Make the song
        </button>
        {job?.status === 'queued' && (
          <button type="button" onClick={cancel} className="min-h-11 px-3 text-sm text-slate-400 flex items-center gap-1">
            <X size={14} /> Cancel
          </button>
        )}
      </div>

      {job && (
        <p className={`text-xs ${job.status === 'error' ? 'text-rose-300' : 'text-slate-300'}`} role="status" data-testid="song-status">
          {songStatus(job)}
          {job.status === 'running' && ' · this can take a few minutes; you can leave this page.'}
          {job.status === 'done' && job.meta?.duration_s ? ` · ${minutes(job.meta.duration_s)}` : ''}
        </p>
      )}
      {error && (
        <p className="text-xs text-rose-300" role="alert">
          {error}
        </p>
      )}

      {job?.status === 'done' && job.audio_url && (
        <audio src={job.audio_url} controls data-testid="music-audio" className="w-full" />
      )}
    </div>
  );
}
