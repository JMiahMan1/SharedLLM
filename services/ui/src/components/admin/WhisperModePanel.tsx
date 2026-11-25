import { useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { MoonStar } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { HubVoiceSettings, WhisperModeSettings } from '../../types/api';
import { errorText } from '../../lib/errorText';
import { SENSITIVITY } from '../../lib/voiceAdmin';

/**
 * Admin > Users & Devices > Whisper mode: a HomeHub voice setting.
 *
 * When someone whispers to a voice satellite (no voiced pitch, and clearly
 * quieter than that satellite usually hears), Jarvis answers at a lower volume
 * for that reply only, holding phrase and hold music included, and puts the
 * volume back once the reply has played. Kept in HomeHub
 * (``PUT /hub/voice/settings``), used from the next command.
 */

const pct = (v: number) => `${Math.round(v * 100)}%`;

export const WhisperModePanel: FC = () => {
  const queryClient = useQueryClient();
  const { data, isPending, isError, error } = useQuery<HubVoiceSettings>({
    queryKey: ['hub-voice-settings'],
    queryFn: () => api.getHubVoiceSettings(),
  });
  const saved = data?.whisper;
  // Unsaved edits; null shows what HomeHub has.
  const [draftVolume, setVolume] = useState<number | null>(null);
  const [draftDrop, setDrop] = useState<number | null>(null);
  const volume = draftVolume ?? saved?.volume ?? null;
  const drop = draftDrop ?? saved?.level_drop_db ?? null;

  const save = useMutation({
    mutationFn: (change: Partial<WhisperModeSettings>) => api.updateHubVoiceSettings(change),
    onSuccess: (next) => {
      queryClient.setQueryData(['hub-voice-settings'], next);
      setVolume(null);
      setDrop(null);
      toast.success('Whisper mode saved');
    },
    onError: (e) => toast.error(errorText(e, 'Whisper mode was not saved')),
  });

  const dirty = !!saved && volume != null && drop != null && (volume !== saved.volume || drop !== saved.level_drop_db);
  const options = saved && !SENSITIVITY.some((s) => s.db === saved.level_drop_db)
    ? [...SENSITIVITY, { label: `Custom (${saved.level_drop_db} dB)`, db: saved.level_drop_db }]
    : SENSITIVITY;

  return (
    <section className="glass-panel min-w-0 overflow-hidden p-4 sm:p-6" data-testid="whisper-mode-panel">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <MoonStar size={20} className="text-indigo-300" />
            Whisper mode
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            Whisper to a voice satellite and Jarvis answers quietly: the satellite&rsquo;s volume goes down for that
            reply only and comes back when it has finished. A HomeHub setting.
          </p>
        </div>
        {saved && (
          <button
            type="button"
            role="switch"
            aria-checked={saved.enabled}
            aria-label="Whisper mode"
            disabled={save.isPending}
            onClick={() => save.mutate({ enabled: !saved.enabled })}
            className={`relative inline-flex h-11 min-w-[5.5rem] shrink-0 items-center rounded-full px-1 text-xs font-bold uppercase tracking-wider transition disabled:opacity-50 ${saved.enabled ? 'bg-indigo-500/80 text-white' : 'bg-white/10 text-slate-400'}`}
          >
            <span className="w-full text-center">{saved.enabled ? 'On' : 'Off'}</span>
          </button>
        )}
      </div>

      {isError && <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200">{errorText(error, 'Could not load HomeHub voice settings')}</p>}
      {isPending && <p className="text-sm text-slate-400">Loading…</p>}

      {saved && volume != null && drop != null && (
        <div className={`grid gap-4 sm:grid-cols-2 ${saved.enabled ? '' : 'opacity-50'}`}>
          <label className="block min-w-0 text-sm text-slate-300">
            <span className="mb-1 flex items-center justify-between gap-2">
              <span>Reply volume</span>
              <span className="font-semibold text-white" data-testid="whisper-volume-value">{pct(volume)}</span>
            </span>
            <input
              type="range" min={0.05} max={1} step={0.01} value={volume}
              disabled={!saved.enabled}
              onChange={(e) => setVolume(Number(e.target.value))}
              aria-label="Reply volume while whispering"
              className="h-11 w-full accent-indigo-400"
            />
          </label>
          <label className="block min-w-0 text-sm text-slate-300">
            <span className="mb-1 block">What counts as a whisper</span>
            <select
              value={drop}
              disabled={!saved.enabled}
              onChange={(e) => setDrop(Number(e.target.value))}
              aria-label="What counts as a whisper"
              className="min-h-11 w-full rounded-xl border border-white/10 bg-slate-900/80 px-3 text-sm text-white"
            >
              {options.map((o) => (
                <option key={o.db} value={o.db}>{o.label}</option>
              ))}
            </select>
          </label>
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            <button
              type="button"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate({ volume: volume, level_drop_db: drop })}
              className="glass-button min-h-11 px-4 text-xs font-black uppercase tracking-widest disabled:opacity-50"
            >
              Save
            </button>
            <p className="min-w-0 flex-1 text-xs text-slate-500">
              A whisper has no voiced pitch and is clearly quieter than that satellite usually hears. Each
              satellite learns its usual level from ordinary commands.
            </p>
          </div>
        </div>
      )}
    </section>
  );
};

export default WhisperModePanel;
