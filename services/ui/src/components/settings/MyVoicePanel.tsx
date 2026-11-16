import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Play, Square, Volume2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { useAuth } from '../../context/AuthContext';
import { api } from '../../services/api';
import { describeVoice, voiceGroups } from '../../lib/voices';

/**
 * The voice Jarvis uses with this person: in their spoken replies and on the
 * speakers and satellites assigned to them. Empty is Jarvis's own voice, which
 * an admin chooses for the household.
 */
export default function MyVoicePanel() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const voices = useQuery({ queryKey: ['tts-voices'], queryFn: () => api.getTtsVoices(), staleTime: 10 * 60_000 });
  // The saved voice, or the one being saved while that is in flight.
  const [pending, setPending] = useState<string | null>(null);
  const chosen = pending ?? user?.preferred_tts_voice ?? '';
  const [saving, setSaving] = useState(false);
  const [playing, setPlaying] = useState<'loading' | 'playing' | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => () => audio.current?.pause(), []);

  const data = voices.data;
  const featured = data?.featured ?? [];
  const jarvis = data?.default ? describeVoice(data.default, featured).name : null;
  const groups = data ? voiceGroups(data.voices, featured) : [];
  const known = !chosen || groups.some((g) => g.voices.some((v) => v.id === chosen));

  const save = async (next: string) => {
    setPending(next);
    setSaving(true);
    try {
      const profile = await api.setMyVoice(next || null);
      queryClient.setQueryData(['me'], profile);
      toast.success(next ? `Jarvis will use ${describeVoice(next, featured).name} with you` : 'Jarvis will use its own voice with you');
    } catch {
      toast.error('Your voice was not saved. Try again.');
    } finally {
      setPending(null);
      setSaving(false);
    }
  };

  const play = async () => {
    if (playing) {
      audio.current?.pause();
      setPlaying(null);
      return;
    }
    setPlaying('loading');
    try {
      const url = await api.ttsSample(chosen || null);
      const el = new Audio(url);
      audio.current = el;
      el.onended = () => setPlaying(null);
      el.onerror = () => setPlaying(null);
      await el.play();
      setPlaying('playing');
    } catch {
      setPlaying(null);
      toast.error('That voice could not be played right now.');
    }
  };

  return (
    <section className="glass-panel rounded-2xl p-4 space-y-3" aria-labelledby="my-voice-heading" data-testid="my-voice-panel">
      <div className="px-1">
        <h2 id="my-voice-heading" className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
          <Volume2 size={16} className="text-purple-400" />
          My voice
        </h2>
        <p className="text-xs text-slate-500 mt-1">
          How Jarvis sounds when it talks to you, including on speakers assigned to you.
        </p>
      </div>
      {voices.isError ? (
        <p className="px-1 text-sm text-amber-300" role="alert">The voices could not be loaded. Check your connection and open Settings again.</p>
      ) : (
        <div className="flex items-stretch gap-2">
          <label className="min-w-0 flex-1">
            <span className="sr-only">Voice</span>
            <select
              value={chosen}
              onChange={(e) => void save(e.target.value)}
              disabled={!data || saving}
              data-testid="my-voice-select"
              className="glass-input w-full min-h-11 px-3 text-base sm:text-sm"
            >
              <option value="">Jarvis&apos;s voice</option>
              {!known && <option value={chosen}>{describeVoice(chosen, featured).name}</option>}
              {groups.map((g) => (
                <optgroup key={g.group} label={g.group}>
                  {g.voices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                </optgroup>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void play()}
            disabled={!data}
            aria-label={playing === 'playing' ? 'Stop the sample' : 'Hear this voice'}
            data-testid="my-voice-play"
            className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl bg-white/5 px-3 text-sm font-medium text-slate-200 hover:bg-white/10 disabled:opacity-50"
          >
            {playing === 'loading' ? <Loader2 size={16} className="animate-spin" /> : playing === 'playing' ? <Square size={14} /> : <Play size={16} />}
            {playing === 'playing' ? 'Stop' : 'Hear it'}
          </button>
        </div>
      )}
      {!chosen && jarvis && (
        <p className="px-1 text-xs text-slate-500" data-testid="my-voice-jarvis">Jarvis&apos;s voice right now: {jarvis}</p>
      )}
    </section>
  );
}
