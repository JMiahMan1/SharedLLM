import { useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Power, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, Home as HomeIcon, Menu, ChevronLeft, Volume2, VolumeX, Tv } from 'lucide-react';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';

const Remote = () => {
  const { trigger } = useHaptics();
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [volume, setVolume] = useState(50);
  const [muted, setMuted] = useState(false);
  const [powerOn, setPowerOn] = useState(true);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: entities = [] } = useQuery({
    queryKey: ['media-entities'],
    queryFn: () => api.getEntities(),
    select: (data) => data.filter((e) => e.domain === 'media_player'),
  });

  const mediaTargets = entities.map((entity) => ({
    id: entity.entity_id,
    name: entity.friendly_name || entity.entity_id,
    room: entity.entity_id.split('.')[1]?.replace(/_/g, ' ') || 'Unknown',
    brand: 'cast' as const,
    online: entity.state !== 'unavailable' && entity.state !== 'unknown',
  }));

  const currentTarget = mediaTargets.find((t) => t.id === selectedTarget);

  const sendTransport = useCallback(async (command: string) => {
    if (!selectedTarget) return;
    trigger('light');
    setLoading(command);
    setError(null);
    try {
      const resp = await api.mediaTransport({ entity_id: selectedTarget, command });
      if (resp.status === 'FAILURE') {
        setError(resp.message || 'Command failed');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Command failed');
    } finally {
      setLoading(null);
    }
  }, [selectedTarget, trigger]);

  const handleDpad = useCallback((action: string) => {
    const commandMap: Record<string, string> = {
      up: 'home',
      down: 'home',
      left: 'back',
      right: 'home',
      ok: 'home',
      back: 'back',
      home: 'home',
      menu: 'home',
    };
    sendTransport(commandMap[action] || 'home');
  }, [sendTransport]);

  const handleVolumeChange = useCallback(async (delta: number) => {
    if (!selectedTarget) return;
    trigger('light');
    const newVolume = Math.min(100, Math.max(0, volume + delta));
    setVolume(newVolume);
    setLoading('volume');
    setError(null);
    try {
      await api.mediaTransport({ entity_id: selectedTarget, command: 'volume_set', volume_level: newVolume / 100 });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Volume change failed');
    } finally {
      setLoading(null);
    }
  }, [selectedTarget, volume, trigger]);

  const handleToggleMute = useCallback(async () => {
    if (!selectedTarget) return;
    trigger('light');
    setMuted((m) => !m);
    setLoading('mute');
    setError(null);
    try {
      await api.mediaTransport({ entity_id: selectedTarget, command: muted ? 'unmute' : 'mute' });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Mute toggle failed');
    } finally {
      setLoading(null);
    }
  }, [selectedTarget, muted, trigger]);

  const handlePower = useCallback(async () => {
    if (!selectedTarget) return;
    trigger('heavy');
    setLoading('power');
    setError(null);
    try {
      const command = powerOn ? 'power_off' : 'home';
      await api.mediaTransport({ entity_id: selectedTarget, command });
      setPowerOn((p) => !p);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Power toggle failed');
    } finally {
      setLoading(null);
    }
  }, [selectedTarget, powerOn, trigger]);

  return (
    <div className="space-y-6 max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold text-white">Remote</h1>

      {error && (
        <div className="bg-red-500/20 border border-red-500/30 rounded-xl p-3 text-red-400 text-sm">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline">Dismiss</button>
        </div>
      )}

      <div className="glass-panel rounded-2xl p-4">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Media Targets</h2>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {mediaTargets.map((target) => (
            <button
              key={target.id}
              onClick={() => { trigger('light'); setSelectedTarget(target.id); }}
              className={`shrink-0 flex items-center gap-2 px-4 py-3 rounded-xl transition-colors min-w-[160px] ${
                selectedTarget === target.id
                  ? 'bg-cyan-500/20 border border-cyan-500/30'
                  : 'bg-white/5 hover:bg-white/10 border border-transparent'
              } ${!target.online ? 'opacity-50' : ''}`}
            >
              <Tv size={16} className="text-cyan-400 shrink-0" />
              <div className="text-left min-w-0">
                <p className="text-white text-sm font-medium truncate">{target.name}</p>
                <p className="text-xs text-slate-400 uppercase">{target.room}</p>
              </div>
            </button>
          ))}
          {mediaTargets.length === 0 && (
            <p className="text-sm text-slate-500 py-4">No media players found. Check Home Assistant connection.</p>
          )}
        </div>
      </div>

      {currentTarget?.online && (
        <>
          <div className="glass-panel rounded-2xl p-6 flex flex-col items-center">
            <div className="grid grid-cols-3 gap-2 w-48">
              <div />
              <button
                onClick={() => handleDpad('up')}
                disabled={loading !== null}
                className="w-14 h-14 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-white hover:bg-white/10 transition-colors active:scale-95 disabled:opacity-50"
              >
                {loading === 'home' ? <span className="animate-spin">⟳</span> : <ArrowUp size={20} />}
              </button>
              <div />

              <button
                onClick={() => handleDpad('left')}
                disabled={loading !== null}
                className="w-14 h-14 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-white hover:bg-white/10 transition-colors active:scale-95 disabled:opacity-50"
              >
                {loading === 'back' ? <span className="animate-spin">⟳</span> : <ArrowLeft size={20} />}
              </button>
              <button
                onClick={() => handleDpad('ok')}
                disabled={loading !== null}
                className="w-14 h-14 rounded-full bg-purple-500/20 border border-purple-500/30 flex items-center justify-center text-purple-400 hover:bg-purple-500/30 transition-colors active:scale-95 disabled:opacity-50"
              >
                {loading === 'home' ? <span className="animate-spin">⟳</span> : 'OK'}
              </button>
              <button
                onClick={() => handleDpad('right')}
                disabled={loading !== null}
                className="w-14 h-14 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-white hover:bg-white/10 transition-colors active:scale-95 disabled:opacity-50"
              >
                {loading === 'home' ? <span className="animate-spin">⟳</span> : <ArrowRight size={20} />}
              </button>

              <div />
              <button
                onClick={() => handleDpad('down')}
                disabled={loading !== null}
                className="w-14 h-14 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-white hover:bg-white/10 transition-colors active:scale-95 disabled:opacity-50"
              >
                {loading === 'home' ? <span className="animate-spin">⟳</span> : <ArrowDown size={20} />}
              </button>
              <div />
            </div>

            <div className="flex items-center gap-4 mt-6">
              <button
                onClick={() => handleDpad('back')}
                disabled={loading !== null}
                className="w-12 h-12 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-white hover:bg-white/10 transition-colors disabled:opacity-50"
              >
                {loading === 'back' ? <span className="animate-spin">⟳</span> : <ChevronLeft size={20} />}
              </button>
              <button
                onClick={() => handleDpad('home')}
                disabled={loading !== null}
                className="w-12 h-12 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-white hover:bg-white/10 transition-colors disabled:opacity-50"
              >
                {loading === 'home' ? <span className="animate-spin">⟳</span> : <HomeIcon size={18} />}
              </button>
              <button
                onClick={() => handleDpad('menu')}
                disabled={loading !== null}
                className="w-12 h-12 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-white hover:bg-white/10 transition-colors disabled:opacity-50"
              >
                {loading === 'home' ? <span className="animate-spin">⟳</span> : <Menu size={18} />}
              </button>
            </div>
          </div>

          <div className="glass-panel rounded-2xl p-4">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Volume2 size={18} className="text-slate-400" />
                <span className="text-sm text-slate-300">Volume</span>
              </div>
              <span className="text-sm text-slate-400">{muted ? 'Muted' : `${volume}%`}</span>
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={() => handleVolumeChange(-10)}
                disabled={loading !== null}
                className="w-10 h-10 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center text-white transition-colors disabled:opacity-50"
              >
                -
              </button>
              <input
                type="range"
                min="0"
                max="100"
                value={muted ? 0 : volume}
                onChange={(e) => { setVolume(Number(e.target.value)); setMuted(false); }}
                className="flex-1 accent-cyan-400"
              />
              <button
                onClick={() => handleVolumeChange(10)}
                disabled={loading !== null}
                className="w-10 h-10 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center text-white transition-colors disabled:opacity-50"
              >
                +
              </button>
              <button
                onClick={handleToggleMute}
                disabled={loading !== null}
                className="w-10 h-10 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center text-white transition-colors disabled:opacity-50"
              >
                {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
              </button>
            </div>
          </div>

          <button
            onClick={handlePower}
            disabled={loading !== null}
            className={`w-full py-4 rounded-2xl border transition-colors flex items-center justify-center gap-2 disabled:opacity-50 ${
              powerOn
                ? 'bg-red-500/10 border-red-500/30 text-red-400 hover:bg-red-500/20'
                : 'bg-green-500/10 border-green-500/30 text-green-400 hover:bg-green-500/20'
            }`}
          >
            {loading === 'power' ? <span className="animate-spin">⟳</span> : <Power size={20} />}
            {powerOn ? 'Power Off' : 'Power On'}
          </button>
        </>
      )}

      {currentTarget && !currentTarget.online && (
        <div className="glass-panel rounded-2xl p-8 text-center">
          <p className="text-slate-400">{currentTarget.name} is offline</p>
        </div>
      )}

      {!selectedTarget && mediaTargets.length > 0 && (
        <div className="glass-panel rounded-2xl p-8 text-center">
          <p className="text-slate-400">Select a media player to control</p>
        </div>
      )}
    </div>
  );
};

export default Remote;
