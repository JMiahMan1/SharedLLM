import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Timer, X, Plus, Loader2, Bell, BellOff, Play, Pause, RotateCcw } from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import { api } from '../../services/api';
import toast from 'react-hot-toast';

interface ActiveTimer {
  id: string;
  title: string;
  durationMs: number;
  remainingMs: number;
  createdAt: number;
  isRemote?: boolean;
  paused?: boolean;
  expiredTicks?: number;
}

interface BackendTimer {
  id: string;
  type?: string;
  title: string;
  expires_at?: string;
  active?: boolean;
  duration_sec?: number;
}

interface MediaPlayer {
  entity_id: string;
  friendly_name: string;
  state: string;
}

const AmbientTimerWidget = ({ userSettings, onTogglePin, settingsButton }: IWidgetProps) => {
  const [timers, setTimers] = useState<ActiveTimer[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [newDuration, setNewDuration] = useState(300);
  const [newTitle, setNewTitle] = useState('');
  const [mediaPlayers, setMediaPlayers] = useState<MediaPlayer[]>([]);
  const [selectedDevice, setSelectedDevice] = useState('');
  const [expiredIds, setExpiredIds] = useState<Set<string>>(new Set());
  const notifiedRef = useRef<Set<string>>(new Set());

  // Fetch HA media players
  useEffect(() => {
    api.getEntities().then((entities) => {
      const players = (entities || [])
        .filter((e: Record<string, unknown>) => e.domain === 'media_player')
        .map((e: Record<string, unknown>) => ({
          entity_id: e.entity_id as string,
          friendly_name: (e.friendly_name as string) || (e.entity_id as string),
          state: e.state as string,
        }));
      setMediaPlayers(players);
    }).catch(() => {});
  }, []);

  const fetchTimers = useCallback(async () => {
    try {
      const backendTimers = await api.getTimers() as BackendTimer[];
      const mapped: ActiveTimer[] = [];
      for (const bt of backendTimers) {
        if (bt.active && bt.expires_at) {
          const expiresAt = new Date(bt.expires_at).getTime();
          const remaining = expiresAt - Date.now();
          if (remaining > 0) {
            const durationMs = bt.duration_sec ? bt.duration_sec * 1000 : remaining;
            const createdAt = expiresAt - durationMs;
            mapped.push({
              id: bt.id,
              title: bt.title || 'Untitled',
              durationMs,
              remainingMs: remaining,
              createdAt,
              isRemote: true,
            });
          }
        }
      }
      // Preserve local-only timers; replace the remote set wholesale.
      setTimers((prev) => {
        const localOnes = prev.filter((t) => t.id.startsWith('local-') && t.remainingMs > 0);
        return [...localOnes, ...mapped];
      });
    } catch {
      // Silently fail - keep local state if backend unavailable
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional fetch-on-mount
    fetchTimers();
    const interval = setInterval(fetchTimers, 10000);
    return () => clearInterval(interval);
  }, [fetchTimers]);

  // Ask for notification permission once on mount
  useEffect(() => {
    try {
      if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
        void Notification.requestPermission();
      }
    } catch { /* ignore */ }
  }, []);

  // Local countdown + expiry detection
  useEffect(() => {
    const interval = setInterval(() => {
      setTimers((prev) => {
        const next: ActiveTimer[] = [];
        for (const t of prev) {
          if (t.paused) { next.push(t); continue; }
          // Already expired: hold a few ticks so the ring-flash is visible (first expiry only)
          if (t.remainingMs <= 0) {
            if (t.expiredTicks === undefined) {
              // Re-fetched timer that hit zero again while already notified — drop now
              continue;
            }
            const ticks = t.expiredTicks + 1;
            if (ticks >= 5) continue;
            next.push({ ...t, remainingMs: 0, expiredTicks: ticks });
            continue;
          }
          const newRemaining = Math.max(0, t.remainingMs - 1000);
          if (newRemaining <= 0) {
            if (!notifiedRef.current.has(t.id)) {
              notifiedRef.current.add(t.id);
              setExpiredIds((prevExpired) => new Set(prevExpired).add(t.id));
              setTimeout(() => {
                setExpiredIds((prevExpired) => {
                  const s = new Set(prevExpired);
                  s.delete(t.id);
                  return s;
                });
              }, 3000);
              if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
                new Notification('Timer Expired', { body: `"${t.title}" is done!` });
              }
              // First expiry: keep on screen for the ring-flash
              next.push({ ...t, remainingMs: 0, expiredTicks: 0 });
            }
            // Already notified (e.g. re-fetched duplicate): drop immediately
            continue;
          }
          next.push({ ...t, remainingMs: newRemaining });
        }
        return next;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const togglePause = useCallback((id: string) => {
    const timer = timers.find((t) => t.id === id);
    if (!timer) return;
    const nextPaused = !timer.paused;
    setTimers((prev) => prev.map((t) => t.id === id ? { ...t, paused: nextPaused } : t));
    if (timer.isRemote) {
      void (async () => {
        try {
          const resp = await api.timerAction(nextPaused ? 'pause' : 'resume', {
            title: timer.title,
            type: 'timer',
            id: timer.id,
          });
          if (resp.status !== 'SUCCESS') {
            // Roll back optimistic pause
            setTimers((prev) => prev.map((t) => t.id === id ? { ...t, paused: !nextPaused } : t));
          }
        } catch {
          setTimers((prev) => prev.map((t) => t.id === id ? { ...t, paused: !nextPaused } : t));
        }
      })();
    }
  }, [timers]);

  const resetTimer = useCallback((id: string) => {
    setTimers((prev) => prev.map((t) => t.id === id ? { ...t, remainingMs: t.durationMs, paused: false } : t));
  }, []);

  const formatTime = (ms: number) => {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    return `${m}:${String(s).padStart(2, '0')}`;
  };

  const totalProgress = useMemo(() => {
    if (timers.length === 0) return 0;
    return timers.reduce(
      (sum, t) => sum + (t.durationMs > 0 ? 1 - t.remainingMs / t.durationMs : 0),
      0
    ) / timers.length * 100;
  }, [timers]);

  const addTimer = async () => {
    if (newDuration < 1) return;
    try {
      const durationSec = Math.floor(newDuration);
      const remote = await api.createTimer({
        title: newTitle || `Timer ${timers.length + 1}`,
        duration_str: `${durationSec}s`,
        type: 'timer',
        target_device: selectedDevice || undefined,
      });
      if (remote.status === 'SUCCESS') {
        const remoteId = (remote as { detail?: { timer_id?: string } }).detail?.timer_id;
        const timer: ActiveTimer = {
          id: remoteId || `local-${Date.now()}`,
          title: newTitle || `Timer ${timers.length + 1}`,
          durationMs: durationSec * 1000,
          remainingMs: durationSec * 1000,
          createdAt: Date.now(),
          isRemote: Boolean(remoteId),
        };
        setTimers((prev) => [...prev, timer]);
        setNewTitle('');
        setNewDuration(300);
        toast.success('Timer created');
        return;
      }
    } catch {
      // Fall back to local-only timer
    }

    const timer: ActiveTimer = {
      id: `local-${Date.now()}`,
      title: newTitle || `Timer ${timers.length + 1}`,
      durationMs: newDuration * 1000,
      remainingMs: newDuration * 1000,
      createdAt: Date.now(),
      isRemote: false,
    };
    setTimers((prev) => [...prev, timer]);
    setNewTitle('');
    setNewDuration(300);
  };

  const removeTimer = async (id: string) => {
    const timer = timers.find((t) => t.id === id);
    if (!timer) return;

    if (timer.isRemote) {
      try {
        const resp = await api.deleteTimer(timer.title, 'timer', timer.id);
        if (resp.status !== 'SUCCESS') {
          toast.error(resp.message || 'Failed to delete timer');
          return;
        }
        toast.success('Timer deleted');
      } catch {
        toast.error('Failed to delete timer');
        return;
      }
    }

    setTimers((prev) => prev.filter((t) => t.id !== id));
  };

  return (
    <div className="glass-card h-full p-5 relative flex flex-col overflow-hidden">
      <div className="absolute top-3 right-3 flex items-center gap-2 z-10">
        <button
          onClick={onTogglePin}
          className="text-slate-500 hover:text-purple-400 transition-colors"
          title={userSettings.is_pinned ? 'Unpin widget' : 'Pin widget'}
        >
          <Timer size={16} className={userSettings.is_pinned ? 'text-purple-400' : ''} />
        </button>
        {settingsButton}
      </div>

      <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2 shrink-0">
        <Timer size={18} className="text-purple-400" />
        Ambient Timer
      </h3>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-3 mb-4 pr-1">
        {isLoading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 size={20} className="animate-spin text-purple-400" />
            <span className="ml-2 text-sm text-slate-500">Loading timers...</span>
          </div>
        ) : timers.length > 0 ? (
          timers.map((timer) => (
            <div
              key={timer.id}
              className={`glass-card p-3 shrink-0 transition-all duration-500 ${
                expiredIds.has(timer.id) ? 'ring-2 ring-purple-400/60 bg-purple-500/10' : ''
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-semibold text-white truncate">{timer.title}</span>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => togglePause(timer.id)}
                    className="text-slate-500 hover:text-purple-400 transition-colors p-0.5"
                    aria-label={timer.paused ? 'Resume' : 'Pause'}
                    title={timer.paused ? 'Resume' : 'Pause'}
                  >
                    {timer.paused ? <Play size={13} /> : <Pause size={13} />}
                  </button>
                  <button
                    onClick={() => resetTimer(timer.id)}
                    className="text-slate-500 hover:text-cyan-400 transition-colors p-0.5"
                    aria-label="Reset"
                    title="Reset"
                  >
                    <RotateCcw size={13} />
                  </button>
                  <button
                    onClick={() => removeTimer(timer.id)}
                    className="text-slate-500 hover:text-red-400 transition-colors p-0.5"
                  >
                    <X size={14} />
                  </button>
                </div>
              </div>
              <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden mb-1">
                <div
                  className="h-full bg-gradient-to-r from-purple-500 to-purple-400 rounded-full transition-all duration-1000"
                  style={{ width: `${timer.durationMs > 0 ? (timer.remainingMs / timer.durationMs) * 100 : 0}%` }}
                />
              </div>
              <p className="text-xs font-mono text-purple-400 shrink-0">{formatTime(timer.remainingMs)}</p>
            </div>
          ))
        ) : (
          <div className="text-center py-6">
            <p className="text-sm text-slate-500">No active timers</p>
          </div>
        )}
      </div>

      <div className="shrink-0 space-y-3">
        <div className="flex gap-2">
          <input
            type="number"
            value={newDuration}
            onChange={(e) => setNewDuration(Math.max(1, Number(e.target.value)))}
            className="glass-input w-20 px-3 py-2 text-sm shrink-0"
            placeholder="Sec"
            min={1}
          />
          <input
            type="text"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            className="glass-input flex-1 px-3 py-2 text-sm min-w-0"
            placeholder="Timer name..."
          />
          <button
            onClick={addTimer}
            className="glass-button px-4 py-2 text-sm text-purple-400 hover:text-purple-300 transition-colors shrink-0"
          >
            <Plus size={16} />
          </button>
        </div>

        {mediaPlayers.length > 0 && (
          <div className="flex items-center gap-2">
            {selectedDevice ? <Bell size={14} className="text-purple-400 shrink-0" /> : <BellOff size={14} className="text-slate-500 shrink-0" />}
            <span className="text-xs text-slate-400">Alert via</span>
            <select
              value={selectedDevice}
              onChange={(e) => setSelectedDevice(e.target.value)}
              className="glass-input flex-1 px-3 py-2 text-sm min-w-0"
            >
              <option value="">No alert (silent)</option>
              {mediaPlayers.map((p) => (
                <option key={p.entity_id} value={p.entity_id}>
                  {p.friendly_name} {p.state !== 'playing' ? `(${p.state})` : ''}
                </option>
              ))}
            </select>
          </div>
        )}

        {timers.length > 0 && (
          <div className="space-y-1">
            <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">Total Progress</p>
            <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-purple-500 to-pink-400 rounded-full transition-all duration-1000"
                style={{ width: `${totalProgress}%` }}
              />
            </div>
            <p className="text-xs text-purple-400">{Math.round(totalProgress)}% complete</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default AmbientTimerWidget;
