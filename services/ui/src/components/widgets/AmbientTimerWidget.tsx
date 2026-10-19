import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import {
  Timer,
  X,
  Plus,
  Loader2,
  Bell,
  BellOff,
  Play,
  Pause,
  RotateCcw,
  AlarmClock,
  Volume2,
  Users,
  Smartphone,
  Monitor,
  SlidersHorizontal,
  Square,
  Zap,
} from 'lucide-react';
import type { IWidgetProps } from '../../types/widget';
import * as chimeLib from '../../lib/chime';
import { playChime, unlockChime } from '../../lib/chime';
import { api } from '../../services/api';
import type { TimerTargetDevice } from '../../types/api';
import { formatClock } from '../../lib/utils';
import toast from 'react-hot-toast';

const FALLBACK_EMOJIS = [
  { emoji: '🥚', label: 'Egg Timer' },
  { emoji: '🍳', label: 'Cooking' },
  { emoji: '⏰', label: 'Alarm Clock' },
  { emoji: '🔔', label: 'Bell' },
  { emoji: '🍕', label: 'Pizza / Oven' },
  { emoji: '🍝', label: 'Pasta' },
  { emoji: '☕', label: 'Coffee / Tea' },
  { emoji: '💊', label: 'Medicine' },
  { emoji: '🏋️', label: 'Workout' },
  { emoji: '📚', label: 'Study / Focus' },
  { emoji: '🛏️', label: 'Bedtime' },
  { emoji: '🌅', label: 'Wake Up' },
  { emoji: '🧹', label: 'Chores' },
  { emoji: '🚨', label: 'Urgent' },
  { emoji: '🎉', label: 'Celebration' },
];

const FALLBACK_SOUNDS = [
  { id: 'classic_alarm', label: 'Classic Digital Alarm' },
  { id: 'egg_timer', label: 'Kitchen Egg Timer' },
  { id: 'gentle_chime', label: 'Gentle Bell Chime' },
  { id: 'morning_melody', label: 'Morning Sunrise Melody' },
  { id: 'retro_arcade', label: 'Retro Arcade Fanfare' },
  { id: 'zen_bowl', label: 'Zen Singing Bowl' },
  { id: 'urgent_siren', label: 'Urgent Hi-Lo Alert' },
  { id: 'rooster', label: 'Rooster Crow Call' },
  { id: 'emoji_sound', label: 'Match Emoji Sound' },
  { id: 'tts_only', label: 'Spoken Voice Only (TTS)' },
];

const EGG_PRESETS = [
  { label: '1m', sec: 60, emoji: '' },
  { label: '3m 🥚', sec: 180, emoji: '🥚', title: 'Soft Boiled Egg' },
  { label: '5m', sec: 300, emoji: '' },
  { label: '7m 🥚', sec: 420, emoji: '🥚', title: 'Medium Boiled Egg' },
  { label: '10m 🥚', sec: 600, emoji: '🥚', title: 'Hard Boiled Egg' },
  { label: '15m', sec: 900, emoji: '' },
];

interface ActiveTimer {
  id: string;
  title: string;
  type: 'timer' | 'alarm' | 'reminder';
  durationMs: number;
  remainingMs: number;
  createdAt: number;
  isRemote?: boolean;
  paused?: boolean;
  expiredTicks?: number;
  scope?: 'user' | 'household' | 'family';
  emoji?: string;
  sound?: string;
  ttsEnabled?: boolean;
  targetDevices?: string[];
  flashScreen?: boolean;
  esphomeScript?: string;
  recurrence?: string;
}

interface BackendTimer {
  id: string;
  type?: 'timer' | 'alarm' | 'reminder' | string;
  title: string;
  expires_at?: string;
  active?: boolean;
  duration_sec?: number;
  /** Set while paused; the server keeps `expires_at` and shifts it on resume. */
  paused_at?: string;
  scope?: 'user' | 'household' | 'family';
  emoji?: string | null;
  sound?: string | null;
  tts_enabled?: boolean;
  target_device?: string | null;
  target_devices?: string[];
  flash_screen?: boolean;
  esphome_script?: string | null;
  recurrence?: string | null;
}

interface MediaPlayer {
  entity_id: string;
  friendly_name: string;
  state: string;
}

const AmbientTimerWidget = ({ settingsButton }: IWidgetProps) => {
  const [timers, setTimers] = useState<ActiveTimer[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [newDuration, setNewDuration] = useState(300);
  const [newTitle, setNewTitle] = useState('');
  const [mediaPlayers, setMediaPlayers] = useState<MediaPlayer[]>([]);
  const [availableDevices, setAvailableDevices] = useState<TimerTargetDevice[]>([]);
  const [selectedDevice, setSelectedDevice] = useState('');
  const [selectedDevices, setSelectedDevices] = useState<string[]>([]);
  const [showOptions, setShowOptions] = useState(false);
  const [entryType, setEntryType] = useState<'timer' | 'alarm' | 'reminder'>('timer');
  const [alarmTime, setAlarmTime] = useState('');
  const [recurrence, setRecurrence] = useState('');
  const [scope, setScope] = useState<'user' | 'household'>('user');
  const [selectedEmoji, setSelectedEmoji] = useState('');
  const [selectedSound, setSelectedSound] = useState('');
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [flashScreen, setFlashScreen] = useState(false);
  const [esphomeScript, setEsphomeScript] = useState('');
  const [ringingTimers, setRingingTimers] = useState<ActiveTimer[]>([]);
  const [expiredIds, setExpiredIds] = useState<Set<string>>(new Set());
  const notifiedRef = useRef<Set<string>>(new Set());

  const emojiList = FALLBACK_EMOJIS;
  const soundList = FALLBACK_SOUNDS;

  const safePlayTimerSound = useCallback((sound?: string, emoji?: string) => {
    try {
      if ('playTimerSound' in chimeLib && typeof chimeLib.playTimerSound === 'function') {
        chimeLib.playTimerSound(sound, emoji);
      }
    } catch { /* ignore in partial mocks */ }
  }, []);

  const safeStartLoopingAlarm = useCallback((sound?: string, emoji?: string, title?: string) => {
    try {
      if ('startLoopingAlarm' in chimeLib && typeof chimeLib.startLoopingAlarm === 'function') {
        chimeLib.startLoopingAlarm(sound, emoji, title);
      }
    } catch { /* ignore in partial mocks */ }
  }, []);

  const safeStopLoopingAlarm = useCallback(() => {
    try {
      if ('stopLoopingAlarm' in chimeLib && typeof chimeLib.stopLoopingAlarm === 'function') {
        chimeLib.stopLoopingAlarm();
      }
    } catch { /* ignore in partial mocks */ }
  }, []);

  // Fetch HA media players and ESPHome/mobile timer devices
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

    if (typeof api.getTimerDevices === 'function') {
      api.getTimerDevices().then((res) => {
        if (res?.devices) {
          setAvailableDevices(res.devices);
        }
      }).catch(() => {});
    }
  }, []);

  const allTargets = useMemo<TimerTargetDevice[]>(() => {
    const map = new Map<string, TimerTargetDevice>();
    map.set('phone', {
      id: 'phone',
      name: 'Mobile App (Phone Alarm)',
      kind: 'mobile',
      has_speaker: true,
      has_screen: true,
    });
    for (const dev of availableDevices) {
      map.set(dev.id, dev);
    }
    for (const mp of mediaPlayers) {
      if (!map.has(mp.entity_id)) {
        map.set(mp.entity_id, {
          id: mp.entity_id,
          name: mp.friendly_name,
          kind: 'ha_media_player',
          has_speaker: true,
          has_screen: false,
          state: mp.state,
        });
      }
    }
    return Array.from(map.values());
  }, [availableDevices, mediaPlayers]);

  const fetchTimers = useCallback(async () => {
    try {
      const backendTimers = await api.getTimers() as BackendTimer[];
      const mapped: ActiveTimer[] = [];
      for (const bt of backendTimers) {
        // A paused timer is `active: false` with a `paused_at`; it used to be
        // dropped here, so pausing a timer made it vanish with no way to resume.
        const paused = !bt.active && Boolean(bt.paused_at);
        if ((bt.active || paused) && bt.expires_at) {
          const expiresAt = new Date(bt.expires_at).getTime();
          const asOf = paused ? new Date(bt.paused_at as string).getTime() : Date.now();
          const remaining = expiresAt - asOf;
          if (remaining > 0) {
            const durationMs = bt.duration_sec ? bt.duration_sec * 1000 : remaining;
            const createdAt = expiresAt - durationMs;
            const tType = (bt.type === 'alarm' || bt.type === 'reminder') ? bt.type : 'timer';
            const targets = bt.target_devices?.length
              ? bt.target_devices
              : (bt.target_device ? [bt.target_device] : []);
            mapped.push({
              id: bt.id,
              title: bt.title || 'Untitled',
              type: tType,
              durationMs,
              remainingMs: remaining,
              createdAt,
              isRemote: true,
              paused,
              scope: bt.scope,
              emoji: bt.emoji || undefined,
              sound: bt.sound || undefined,
              ttsEnabled: bt.tts_enabled,
              targetDevices: targets,
              flashScreen: bt.flash_screen,
              esphomeScript: bt.esphome_script || undefined,
              recurrence: bt.recurrence || undefined,
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
              // Re-fetched timer that hit zero again while already notified; drop now
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
              playChime();
              if (t.sound || t.emoji) {
                safePlayTimerSound(t.sound, t.emoji);
              }
              if (t.type === 'alarm' || t.flashScreen || (t.targetDevices && t.targetDevices.length > 0)) {
                setRingingTimers((prevRinging) => (
                  prevRinging.some((r) => r.id === t.id) ? prevRinging : [...prevRinging, t]
                ));
                if (t.type === 'alarm') {
                  safeStartLoopingAlarm(t.sound, t.emoji, t.title);
                }
              }
              if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
                const prefix = t.emoji ? `${t.emoji} ` : '';
                new Notification(t.type === 'alarm' ? 'Alarm Ringing' : 'Timer Expired', {
                  body: `${prefix}"${t.title}" is done!`,
                });
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
  }, [safePlayTimerSound, safeStartLoopingAlarm]);

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
            type: timer.type,
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
    const timer = timers.find((t) => t.id === id);
    if (!timer) return;
    notifiedRef.current.delete(id);
    setTimers((prev) => prev.map((t) => t.id === id ? { ...t, remainingMs: t.durationMs, paused: false, expiredTicks: undefined } : t));
    if (timer.isRemote && typeof api.timerAction === 'function') {
      void api.timerAction('reset', {
        title: timer.title,
        type: timer.type,
        id: timer.id,
      }).catch(() => {});
    }
  }, [timers]);

  const stopRinging = useCallback(async (timer?: ActiveTimer) => {
    safeStopLoopingAlarm();
    if (timer) {
      setRingingTimers((prev) => prev.filter((r) => r.id !== timer.id));
    } else {
      setRingingTimers([]);
    }
    if (typeof api.timerAction === 'function') {
      try {
        await api.timerAction('stop', {
          title: timer?.title || '',
          type: timer?.type || 'alarm',
          id: timer?.id,
        });
        toast.success('Stopped alarm & screen alerts');
      } catch {
        // Ignore network errors when silencing locally
      }
    }
  }, [safeStopLoopingAlarm]);

  const toggleTargetDevice = (deviceId: string) => {
    setSelectedDevices((prev) => {
      const next = prev.includes(deviceId)
        ? prev.filter((d) => d !== deviceId)
        : [...prev, deviceId];
      setSelectedDevice(next[0] || '');
      return next;
    });
  };

  const formatTime = (ms: number) => formatClock(Math.max(0, ms) / 1000);

  const totalProgress = useMemo(() => {
    if (timers.length === 0) return 0;
    return timers.reduce(
      (sum, t) => sum + (t.durationMs > 0 ? 1 - t.remainingMs / t.durationMs : 0),
      0
    ) / timers.length * 100;
  }, [timers]);

  const addTimer = async () => {
    if (entryType === 'timer' && newDuration < 1 && !alarmTime.trim()) return;
    // This tap is the gesture that lets the chime play when the timer ends.
    unlockChime();
    // Ask for the expiry notification when a timer is actually set, not as a
    // browser prompt the moment the dashboard opens.
    try {
      if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
        void Notification.requestPermission();
      }
    } catch { /* ignore */ }

    const defaultLabel = entryType === 'alarm'
      ? `Alarm ${timers.length + 1}`
      : entryType === 'reminder'
      ? `Reminder ${timers.length + 1}`
      : `Timer ${timers.length + 1}`;
    const resolvedTitle = newTitle.trim() || defaultLabel;

    try {
      const durationSec = Math.floor(newDuration);
      const mergedDevices = selectedDevices.length > 0
        ? selectedDevices
        : (selectedDevice ? [selectedDevice] : []);

      const payload: Parameters<typeof api.createTimer>[0] = {
        title: resolvedTitle,
        duration_str: alarmTime.trim() ? undefined : `${durationSec}s`,
        type: entryType,
        target_device: mergedDevices[0] || selectedDevice || undefined,
      };

      if (alarmTime.trim()) {
        payload.time_str = alarmTime.trim();
      }
      if (mergedDevices.length > 1) {
        payload.target_devices = mergedDevices;
      }
      if (scope === 'household') {
        payload.scope = 'household';
      }
      if (selectedEmoji) {
        payload.emoji = selectedEmoji;
      }
      if (selectedSound) {
        payload.sound = selectedSound;
      }
      if (!ttsEnabled) {
        payload.tts_enabled = false;
      }
      if (flashScreen) {
        payload.flash_screen = true;
      }
      if (esphomeScript.trim()) {
        payload.esphome_script = esphomeScript.trim();
      }
      if (recurrence) {
        payload.recurrence = recurrence;
      }

      const remote = await api.createTimer(payload);
      if (remote.status === 'SUCCESS') {
        const detail = (remote as { detail?: { timer_id?: string; expires_at?: string } }).detail;
        const remoteId = detail?.timer_id;
        const computedMs = detail?.expires_at
          ? Math.max(1000, new Date(detail.expires_at).getTime() - Date.now())
          : durationSec * 1000;
        const timer: ActiveTimer = {
          id: remoteId || `local-${Date.now()}`,
          title: resolvedTitle,
          type: entryType,
          durationMs: computedMs,
          remainingMs: computedMs,
          createdAt: Date.now(),
          isRemote: Boolean(remoteId),
          scope,
          emoji: selectedEmoji || undefined,
          sound: selectedSound || undefined,
          ttsEnabled,
          targetDevices: mergedDevices,
          flashScreen,
          esphomeScript: esphomeScript.trim() || undefined,
          recurrence: recurrence || undefined,
        };
        setTimers((prev) => [...prev, timer]);
        setNewTitle('');
        setNewDuration(300);
        setAlarmTime('');
        toast.success(entryType === 'alarm' ? 'Alarm set' : entryType === 'reminder' ? 'Reminder set' : 'Timer created');
        return;
      }
    } catch {
      // Fall back to local-only timer
    }

    const timer: ActiveTimer = {
      id: `local-${Date.now()}`,
      title: resolvedTitle,
      type: entryType,
      durationMs: newDuration * 1000,
      remainingMs: newDuration * 1000,
      createdAt: Date.now(),
      isRemote: false,
      scope,
      emoji: selectedEmoji || undefined,
      sound: selectedSound || undefined,
      ttsEnabled,
      flashScreen,
    };
    setTimers((prev) => [...prev, timer]);
    setNewTitle('');
    setNewDuration(300);
  };

  const removeTimer = async (id: string) => {
    const timer = timers.find((t) => t.id === id);
    if (!timer) return;

    safeStopLoopingAlarm();
    setRingingTimers((prev) => prev.filter((r) => r.id !== id));

    if (timer.isRemote) {
      try {
        const resp = await api.deleteTimer(timer.title, timer.type, timer.id);
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

  const hasScreenlessSpeakerTarget = useMemo(() => {
    const activeIds = selectedDevices.length > 0 ? selectedDevices : (selectedDevice ? [selectedDevice] : []);
    return activeIds.some((id) => {
      const dev = allTargets.find((d) => d.id === id);
      return dev && (!dev.has_speaker || dev.kind === 'esphome');
    });
  }, [selectedDevices, selectedDevice, allTargets]);

  return (
    <div className="glass-card h-full p-5 relative flex flex-col overflow-hidden">
      <div className="absolute top-3 right-3 z-10 flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setShowOptions((prev) => !prev)}
          aria-label="Timer & alarm options"
          title="Alarms, Sounds, Emojis & Devices"
          className={`p-1.5 rounded-lg transition-colors ${
            showOptions
              ? 'bg-purple-500/20 text-purple-300 ring-1 ring-purple-400/40'
              : 'text-slate-400 hover:text-purple-300 hover:bg-white/5'
          }`}
        >
          <SlidersHorizontal size={15} />
        </button>
        {settingsButton}
      </div>

      <h3 className="text-lg font-bold text-white mb-3 flex items-center gap-2 shrink-0">
        <Timer size={18} className="text-purple-400" />
        <span>Ambient Timer</span>
        {ringingTimers.length > 0 && (
          <button
            type="button"
            onClick={() => void stopRinging()}
            className="ml-auto mr-8 flex items-center gap-1 rounded-lg bg-red-500/20 border border-red-400/40 px-2.5 py-1 text-xs font-bold text-red-200 animate-pulse hover:bg-red-500/30"
          >
            <Square size={11} />
            Stop Alarm
          </button>
        )}
      </h3>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-3 mb-3 pr-1">
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
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-1.5 min-w-0 flex-1">
                  {timer.emoji && <span className="text-sm shrink-0">{timer.emoji}</span>}
                  {timer.type === 'alarm' && !timer.emoji && <AlarmClock size={13} className="text-amber-300 shrink-0" />}
                  <span className="text-sm font-semibold text-white truncate">
                    {timer.title}
                  </span>
                  {timer.scope === 'household' && (
                    <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-[10px] font-medium text-emerald-300 shrink-0">
                      Family
                    </span>
                  )}
                  {timer.paused && <span className="ml-1 text-[10px] font-normal text-amber-300 shrink-0">Paused</span>}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => togglePause(timer.id)}
                    className="text-slate-500 hover:text-purple-400 transition-colors p-0.5 pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:flex pointer-coarse:items-center pointer-coarse:justify-center"
                    aria-label={timer.paused ? 'Resume' : 'Pause'}
                    title={timer.paused ? 'Resume' : 'Pause'}
                  >
                    {timer.paused ? <Play size={13} /> : <Pause size={13} />}
                  </button>
                  {timer.durationMs > 0 && (
                    <button
                      onClick={() => resetTimer(timer.id)}
                      className="text-slate-500 hover:text-cyan-400 transition-colors p-0.5 pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:flex pointer-coarse:items-center pointer-coarse:justify-center"
                      aria-label="Reset"
                      title="Reset"
                    >
                      <RotateCcw size={13} />
                    </button>
                  )}
                  <button
                    onClick={() => removeTimer(timer.id)}
                    className="text-slate-500 hover:text-red-400 transition-colors p-0.5 pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:flex pointer-coarse:items-center pointer-coarse:justify-center"
                    aria-label={`Delete ${timer.title}`}
                    title="Delete"
                  >
                    <X size={14} data-lucide="x" />
                  </button>
                </div>
              </div>
              <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden mb-1">
                <div
                  className="h-full bg-gradient-to-r from-purple-500 to-purple-400 rounded-full transition-all duration-1000"
                  style={{ width: `${timer.durationMs > 0 ? (timer.remainingMs / timer.durationMs) * 100 : 0}%` }}
                />
              </div>
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-mono text-purple-400 shrink-0">{formatTime(timer.remainingMs)}</p>
                {timer.targetDevices && timer.targetDevices.length > 0 && (
                  <span className="text-[10px] text-slate-400 truncate">
                    {timer.targetDevices.length === 1
                      ? timer.targetDevices[0].replace(/^media_player\./, '')
                      : `${timer.targetDevices.length} devices`}
                    {timer.flashScreen ? ' • ⚡ Flash' : ''}
                  </span>
                )}
              </div>
            </div>
          ))
        ) : (
          <div className="text-center py-6">
            <p className="text-sm text-slate-500">No active timers</p>
          </div>
        )}
      </div>

      <div className="shrink-0 space-y-2.5">
        {showOptions && (
          <div className="rounded-xl border border-white/10 bg-black/30 p-3 space-y-2.5 max-h-60 overflow-y-auto">
            {/* Mode & Scope row */}
            <div className="flex flex-wrap items-center justify-between gap-1.5">
              <div className="flex items-center gap-1">
                {(['timer', 'alarm', 'reminder'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => setEntryType(m)}
                    className={`px-2 py-1 rounded-lg text-[11px] font-semibold capitalize transition-colors ${
                      entryType === m
                        ? 'bg-purple-500/30 text-purple-200 border border-purple-400/40'
                        : 'bg-white/5 text-slate-400 hover:text-white'
                    }`}
                  >
                    {m === 'timer' ? 'Egg Timer' : m}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={() => setScope((prev) => (prev === 'household' ? 'user' : 'household'))}
                className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold transition-colors ${
                  scope === 'household'
                    ? 'bg-emerald-500/25 text-emerald-200 border border-emerald-400/40'
                    : 'bg-white/5 text-slate-400 hover:text-white'
                }`}
                title="Notify the whole household (Admin reminder/alarm)"
              >
                <Users size={11} />
                {scope === 'household' ? 'Household' : 'Personal'}
              </button>
            </div>

            {/* Quick minute / egg timer presets */}
            {entryType === 'timer' && (
              <div className="flex flex-wrap gap-1">
                {EGG_PRESETS.map((preset) => (
                  <button
                    key={preset.label}
                    type="button"
                    onClick={() => {
                      setNewDuration(preset.sec);
                      if (preset.emoji) setSelectedEmoji(preset.emoji);
                      if (preset.title && !newTitle) setNewTitle(preset.title);
                    }}
                    className="px-2 py-0.5 rounded-md bg-white/5 hover:bg-purple-500/20 text-[11px] text-slate-300 border border-white/5"
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            )}

            {/* Alarm time & Recurrence */}
            {(entryType === 'alarm' || entryType === 'reminder') && (
              <div className="flex gap-2">
                <input
                  type="text"
                  value={alarmTime}
                  onChange={(e) => setAlarmTime(e.target.value)}
                  placeholder="7:30am or 19:30"
                  aria-label="Alarm time"
                  className="glass-input flex-1 px-2.5 py-1.5 text-xs min-w-0"
                />
                <input
                  type="text"
                  value={recurrence}
                  onChange={(e) => setRecurrence(e.target.value)}
                  placeholder="daily / weekdays"
                  aria-label="Recurrence"
                  className="glass-input w-28 px-2.5 py-1.5 text-xs shrink-0"
                />
              </div>
            )}

            {/* Emoji sound picker */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">Emoji & Sound</span>
                {selectedEmoji && (
                  <button
                    type="button"
                    onClick={() => setSelectedEmoji('')}
                    className="text-[10px] text-slate-400 hover:text-white"
                  >
                    Clear {selectedEmoji}
                  </button>
                )}
              </div>
              <div className="flex flex-wrap gap-1">
                {emojiList.map((item) => (
                  <button
                    key={item.emoji}
                    type="button"
                    onClick={() => {
                      const next = selectedEmoji === item.emoji ? '' : item.emoji;
                      setSelectedEmoji(next);
                      if (next) {
                        safePlayTimerSound('emoji_sound', next);
                      }
                    }}
                    title={item.label}
                    className={`w-7 h-7 rounded-lg flex items-center justify-center text-sm transition-transform ${
                      selectedEmoji === item.emoji
                        ? 'bg-purple-500/30 ring-1 ring-purple-400 scale-110'
                        : 'bg-white/5 hover:bg-white/10'
                    }`}
                  >
                    {item.emoji}
                  </button>
                ))}
              </div>
            </div>

            {/* Alarm melody / sound selector + Preview + TTS */}
            <div className="space-y-1.5">
              <div className="flex flex-wrap gap-1">
                {soundList.map((snd) => (
                  <button
                    key={snd.id}
                    type="button"
                    onClick={() => {
                      const next = selectedSound === snd.id ? '' : snd.id;
                      setSelectedSound(next);
                      if (next) {
                        safePlayTimerSound(next, selectedEmoji || undefined);
                      }
                    }}
                    className={`px-2 py-1 rounded-md text-[10px] font-medium transition-colors ${
                      selectedSound === snd.id
                        ? 'bg-purple-500/30 text-purple-200 border border-purple-400/40'
                        : 'bg-white/5 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {snd.label}
                  </button>
                ))}
              </div>
              <div className="flex items-center justify-between gap-2 pt-0.5">
                <button
                  type="button"
                  onClick={() => setTtsEnabled((prev) => !prev)}
                  className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-colors ${
                    ttsEnabled
                      ? 'bg-cyan-500/20 text-cyan-200 border border-cyan-400/30'
                      : 'bg-white/5 text-slate-400'
                  }`}
                >
                  <Volume2 size={12} />
                  {ttsEnabled ? 'Spoken TTS On' : 'Spoken TTS Off'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    safePlayTimerSound(selectedSound || undefined, selectedEmoji || undefined);
                    playChime();
                  }}
                  className="px-2 py-1 rounded-lg bg-white/5 hover:bg-white/10 text-[11px] text-purple-300"
                >
                  Preview Sound
                </button>
              </div>
            </div>

            {/* Multi-device target picker */}
            <div>
              <span className="block text-[10px] uppercase tracking-wider text-slate-400 font-semibold mb-1">
                Notify Devices (Multi-Select)
              </span>
              <div className="flex flex-wrap gap-1.5">
                {allTargets.map((dev) => {
                  const active = selectedDevices.includes(dev.id) || (!selectedDevices.length && selectedDevice === dev.id);
                  const isTv = dev.kind === 'tv' || ['roku', 'webos', 'samsung', 'android_tv', 'cast', 'bravia'].includes(dev.platform || '');
                  return (
                    <button
                      key={dev.id}
                      type="button"
                      onClick={() => toggleTargetDevice(dev.id)}
                      title={dev.os_version ? `${dev.name} (${dev.os_version})` : dev.name}
                      className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] transition-colors ${
                        active
                          ? 'bg-purple-500/30 text-purple-200 border border-purple-400/50'
                          : 'bg-white/5 text-slate-400 hover:text-white border border-transparent'
                      }`}
                    >
                      {dev.kind === 'mobile' || dev.kind === 'phone' ? (
                        <Smartphone size={11} />
                      ) : isTv ? (
                        <Monitor size={11} className="text-sky-300" />
                      ) : !dev.has_speaker && dev.has_screen ? (
                        <Monitor size={11} className="text-amber-300" />
                      ) : (
                        <Bell size={11} />
                      )}
                      <span className="truncate max-w-[130px]">{dev.name}</span>
                      {isTv && dev.platform && (
                        <span className="text-[9px] text-sky-300 uppercase">({dev.platform.replace(/_/g, ' ')})</span>
                      )}
                      {!dev.has_speaker && dev.has_screen && (
                        <span className="text-[9px] text-amber-300">(Screen)</span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Speakerless / ESPHome screen flash & script controls */}
            {(hasScreenlessSpeakerTarget || flashScreen || esphomeScript) && (
              <div className="rounded-lg bg-amber-500/10 border border-amber-400/20 p-2 space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-amber-200 flex items-center gap-1 font-medium">
                    <Zap size={12} />
                    ESPHome Screen Alert
                  </span>
                  <button
                    type="button"
                    onClick={() => setFlashScreen((prev) => !prev)}
                    className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                      flashScreen
                        ? 'bg-amber-400/30 text-amber-100 border border-amber-300/40'
                        : 'bg-white/5 text-slate-300'
                    }`}
                  >
                    {flashScreen ? 'Flash Until Button Pressed: ON' : 'Flash Screen: Auto'}
                  </button>
                </div>
                <input
                  type="text"
                  value={esphomeScript}
                  onChange={(e) => setEsphomeScript(e.target.value)}
                  placeholder="Optional ESPHome script (e.g. flash_screen, alarm_flash)"
                  aria-label="ESPHome script name"
                  className="glass-input w-full px-2 py-1 text-xs"
                />
              </div>
            )}
          </div>
        )}

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
            aria-label="Add timer"
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
              onChange={(e) => {
                setSelectedDevice(e.target.value);
                setSelectedDevices(e.target.value ? [e.target.value] : []);
              }}
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

