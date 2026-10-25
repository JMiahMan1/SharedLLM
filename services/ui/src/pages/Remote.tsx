import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ChevronLeft, Home as HomeIcon, Loader2, Menu,
  Pause, Play, Power, SkipBack, SkipForward, Speaker, Tv, Volume2, VolumeX, Minus, Plus,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';
import { errorText } from '../lib/errorText';
import { formatDeviceLabel } from '../lib/deviceLabels';
import type { MediaStatus } from '../features/media/types';

type RemoteCommand =
  | 'up' | 'down' | 'left' | 'right' | 'select' | 'back' | 'home' | 'menu'
  | 'power_on' | 'power_off' | 'pause' | 'resume' | 'next' | 'previous';

const POLL_MS = 5000;
const VOLUME_GRACE_MS = 4000;

const keyClass =
  'min-w-14 min-h-14 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-white hover:bg-white/10 active:scale-95 active:bg-white/15 transition disabled:opacity-40';

const isOnline = (p: MediaStatus) => p.state !== 'unavailable' && p.state !== 'unknown';
const isOff = (p: MediaStatus) => p.state === 'off' || p.state === 'standby';

/**
 * A TV remote for Home Assistant media players: d-pad, back/home/menu, power,
 * playback and volume. Every button sends a real key (`/execute/media/transport`
 * routes it to the TV brand's remote, or HA's media_player service), and the
 * volume, mute and power state shown are the device's own, polled every 5 s.
 */
const Remote = () => {
  const { trigger } = useHaptics();
  const [selectedId, setSelectedId] = useState('');
  const [pending, setPending] = useState<string | null>(null);
  const [volume, setVolume] = useState<number | null>(null);
  const [muted, setMuted] = useState(false);
  const lastVolumeChange = useRef(0);
  const volumeTimer = useRef<number | null>(null);

  const status = useQuery({
    queryKey: ['remote-media-status'],
    queryFn: async () => {
      const resp = await api.mediaStatus();
      const all = ((resp.detail as { all_players?: MediaStatus[] } | undefined)?.all_players || [])
        .filter((p) => p.entity_id && p.entity_id !== 'web_player');
      // TVs first, then whatever is reachable.
      return all.sort((a, b) =>
        Number(b.icon_kind === 'tv') - Number(a.icon_kind === 'tv') || Number(isOnline(b)) - Number(isOnline(a)));
    },
    refetchInterval: () => (document.hidden ? false : POLL_MS),
  });
  const players = useMemo(() => status.data ?? [], [status.data]);

  // Start on a TV that is on, else any reachable TV or speaker.
  useEffect(() => {
    if (selectedId || !players.length) return;
    const first =
      players.find((p) => p.icon_kind === 'tv' && isOnline(p) && !isOff(p)) ??
      players.find((p) => p.icon_kind === 'tv' && isOnline(p)) ??
      players.find(isOnline);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time default choice
    if (first?.entity_id) setSelectedId(first.entity_id);
  }, [players, selectedId]);

  const current = players.find((p) => p.entity_id === selectedId);

  // Mirror the device's volume and mute unless the person just changed them.
  useEffect(() => {
    if (!current || Date.now() - lastVolumeChange.current < VOLUME_GRACE_MS) return;
    setVolume(typeof current.volume_level === 'number' ? Math.round(current.volume_level * 100) : null);
    setMuted(Boolean(current.is_volume_muted));
  }, [current]);

  const send = useCallback(async (command: RemoteCommand | 'volume_set' | 'volume_mute' | 'volume_up' | 'volume_down', extra: { volume_level?: number; muted?: boolean } = {}) => {
    if (!selectedId) return false;
    void trigger(command.startsWith('power') ? 'heavy' : 'light');
    setPending(command);
    try {
      const resp = await api.mediaTransport({ entity_id: selectedId, command, ...extra });
      if (resp.status === 'FAILURE') throw new Error(resp.message || 'The device did not accept that');
      return true;
    } catch (err) {
      toast.error(errorText(err, 'The device did not respond'));
      return false;
    } finally {
      setPending(null);
      if (command.startsWith('power') || command === 'pause' || command === 'resume') void status.refetch();
    }
  }, [selectedId, trigger, status]);

  const changeVolume = useCallback((v: number) => {
    const level = Math.max(0, Math.min(100, Math.round(v)));
    lastVolumeChange.current = Date.now();
    setVolume(level);
    setMuted(false);
    if (volumeTimer.current) clearTimeout(volumeTimer.current);
    volumeTimer.current = window.setTimeout(() => {
      void send('volume_set', { volume_level: level / 100 });
    }, 250);
  }, [send]);

  const stepVolume = useCallback((delta: number) => {
    // Devices that report no level only understand relative steps.
    if (volume === null) void send(delta > 0 ? 'volume_up' : 'volume_down');
    else changeVolume(volume + delta);
  }, [volume, changeVolume, send]);

  const toggleMute = useCallback(async () => {
    const next = !muted;
    lastVolumeChange.current = Date.now();
    setMuted(next);
    if (!(await send('volume_mute', { muted: next }))) setMuted(!next);
  }, [muted, send]);

  const key = (command: RemoteCommand, label: string, icon: ReactNode, className = keyClass) => (
    <button type="button" onClick={() => void send(command)} className={className} aria-label={label} disabled={!current || !isOnline(current)}>
      {pending === command ? <Loader2 size={18} className="animate-spin" /> : icon}
    </button>
  );

  const off = current ? isOff(current) : false;
  const playing = current?.state === 'playing';

  return (
    <div className="space-y-5 max-w-2xl mx-auto pb-24">
      <h1 className="text-2xl font-bold text-white">Remote</h1>

      <div className="glass-panel rounded-2xl p-4">
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Devices</h2>
        {status.isLoading ? (
          <div className="flex justify-center py-6" role="status" aria-label="Loading"><Loader2 className="animate-spin text-slate-500" /></div>
        ) : status.isError ? (
          <div className="text-center py-4">
            <p className="text-sm text-red-300">Couldn’t reach Home Assistant.</p>
            <button type="button" onClick={() => void status.refetch()} className="mt-1 min-h-11 px-4 text-sm text-cyan-300 underline">Try again</button>
          </div>
        ) : players.length === 0 ? (
          <p className="text-sm text-slate-500 py-4">No TVs or speakers found in Home Assistant.</p>
        ) : (
          <div className="flex gap-2 overflow-x-auto sm:flex-wrap sm:overflow-visible pb-1 custom-scrollbar">
            {players.map((p) => {
              const selected = p.entity_id === selectedId;
              const Icon = p.icon_kind === 'tv' ? Tv : Speaker;
              return (
                <button
                  type="button"
                  key={p.entity_id}
                  aria-pressed={selected}
                  onClick={() => { void trigger('light'); setSelectedId(p.entity_id as string); }}
                  className={`shrink-0 flex items-center gap-2 px-3.5 py-2.5 min-h-11 rounded-xl border transition-colors min-w-[150px] max-w-[220px] text-left ${
                    selected ? 'bg-cyan-500/20 border-cyan-500/40' : 'bg-white/5 border-white/10 hover:bg-white/10'
                  } ${isOnline(p) ? '' : 'opacity-50'}`}
                >
                  <Icon size={16} className="text-cyan-400 shrink-0" />
                  <span className="min-w-0">
                    <span className="block text-white text-sm font-medium truncate">{formatDeviceLabel(p.friendly_name, p.entity_id)}</span>
                    <span className="block text-[11px] text-slate-400 capitalize truncate">{isOnline(p) ? p.state : 'offline'}</span>
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {current && !isOnline(current) && (
        <div className="glass-panel rounded-2xl p-8 text-center">
          <p className="text-slate-300">{formatDeviceLabel(current.friendly_name, current.entity_id)} is offline</p>
          <p className="text-xs text-slate-500 mt-1">Home Assistant can’t reach it right now.</p>
        </div>
      )}

      {current && isOnline(current) && (
        <>
          <div className="glass-panel rounded-2xl p-4 flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-white font-medium truncate">{formatDeviceLabel(current.friendly_name, current.entity_id)}</p>
              <p className="text-xs text-slate-400 truncate">
                {off ? 'Off' : current.media_title ? `${current.media_title}${current.media_artist ? ` · ${current.media_artist}` : ''}` : current.app_name || current.state}
              </p>
            </div>
            <button
              type="button"
              onClick={() => void send(off ? 'power_on' : 'power_off')}
              className={`min-h-11 px-4 rounded-xl border flex items-center gap-2 shrink-0 transition-colors ${
                off ? 'bg-green-500/10 border-green-500/30 text-green-300' : 'bg-red-500/10 border-red-500/30 text-red-300'
              }`}
            >
              {pending?.startsWith('power') ? <Loader2 size={18} className="animate-spin" /> : <Power size={18} />}
              {off ? 'Turn on' : 'Turn off'}
            </button>
          </div>

          <div className="glass-panel rounded-2xl p-5 flex flex-col items-center gap-5">
            <div className="grid grid-cols-3 gap-2" role="group" aria-label="Direction pad">
              <span />
              {key('up', 'Up', <ArrowUp size={22} />)}
              <span />
              {key('left', 'Left', <ArrowLeft size={22} />)}
              {key('select', 'OK', <span className="font-semibold">OK</span>,
                'min-w-14 min-h-14 rounded-full bg-purple-500/20 border border-purple-500/30 flex items-center justify-center text-purple-200 hover:bg-purple-500/30 active:scale-95 transition disabled:opacity-40')}
              {key('right', 'Right', <ArrowRight size={22} />)}
              <span />
              {key('down', 'Down', <ArrowDown size={22} />)}
              <span />
            </div>
            <div className="flex items-center gap-3">
              {key('back', 'Back', <ChevronLeft size={22} />)}
              {key('home', 'Home', <HomeIcon size={20} />)}
              {key('menu', 'Menu', <Menu size={20} />)}
            </div>
            <div className="flex items-center gap-3">
              {key('previous', 'Previous', <SkipBack size={20} />)}
              {key(playing ? 'pause' : 'resume', playing ? 'Pause' : 'Play', playing ? <Pause size={22} /> : <Play size={22} />)}
              {key('next', 'Next', <SkipForward size={20} />)}
            </div>
          </div>

          <div className="glass-panel rounded-2xl p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm text-slate-300 flex items-center gap-2"><Volume2 size={16} className="text-slate-400" />Volume</span>
              <span className="text-sm text-slate-400 tabular-nums">{muted ? 'Muted' : volume === null ? 'No data' : `${volume}%`}</span>
            </div>
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => stepVolume(-5)} className="min-w-11 min-h-11 rounded-xl bg-white/5 hover:bg-white/10 flex items-center justify-center text-white" aria-label="Volume down">
                <Minus size={18} />
              </button>
              <input
                type="range"
                min="0"
                max="100"
                value={muted ? 0 : volume ?? 0}
                disabled={volume === null}
                onChange={(e) => changeVolume(Number(e.target.value))}
                className="flex-1 min-w-0 h-11 accent-cyan-400 disabled:opacity-40"
                aria-label="Volume"
              />
              <button type="button" onClick={() => stepVolume(5)} className="min-w-11 min-h-11 rounded-xl bg-white/5 hover:bg-white/10 flex items-center justify-center text-white" aria-label="Volume up">
                <Plus size={18} />
              </button>
              <button type="button" onClick={() => void toggleMute()} aria-pressed={muted} className="min-w-11 min-h-11 rounded-xl bg-white/5 hover:bg-white/10 flex items-center justify-center text-white" aria-label={muted ? 'Unmute' : 'Mute'}>
                {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default Remote;
