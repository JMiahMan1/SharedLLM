import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlarmClock,
  BellRing,
  Clock3,
  Megaphone,
  Monitor,
  Pause,
  Play,
  Plus,
  RotateCcw,
  SlidersHorizontal,
  Smartphone,
  Square,
  Trash2,
  Users,
  Volume2,
  Zap,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import { ALARM_SOUNDS, EMOJI_PRESETS, playTimerSound, stopLoopingAlarm, unlockChime } from '../../lib/chime';
import type { DeviceAssignment, TimerDevicesResponse, TimerRecord, TimerTargetDevice } from '../../services/api';

const EMPTY_TIMERS: TimerRecord[] = [];
const EMPTY_DEVICES: DeviceAssignment[] = [];

const QUICK_PRESETS = [
  { label: '1m', duration: '1m', emoji: '' },
  { label: '3m 🥚 Soft Egg', duration: '3m', emoji: '🥚', title: 'Soft Boiled Egg' },
  { label: '5m', duration: '5m', emoji: '' },
  { label: '7m 🥚 Jammy Egg', duration: '7m', emoji: '🥚', title: 'Medium Boiled Egg' },
  { label: '10m 🥚 Hard Egg', duration: '10m', emoji: '🥚', title: 'Hard Boiled Egg' },
  { label: '15m', duration: '15m', emoji: '' },
  { label: '30m', duration: '30m', emoji: '' },
];

/** "media_player.kitchen_speaker" -> "Kitchen speaker". */
function deviceLabel(deviceId: string): string {
  // The phone/tablet app registers under one of these ids; the app runs on
  // either form factor, so the label does not claim "phone".
  if (deviceId === 'phone' || deviceId === 'mobile' || deviceId === 'apk') return 'Mobile App';
  const name = deviceId
    .replace(/^media_player\./, '')
    .replace(/^esphome[:.]/, '')
    .replace(/_/g, ' ')
    .trim();
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : deviceId;
}

/**
 * Timers, alarms, household reminders, and spoken announcements - the panel
 * behind the Family page's Announce tab.
 */
const AnnouncementsPanel = () => {
  const queryClient = useQueryClient();
  const { trigger } = useHaptics();
  const [timerTitle, setTimerTitle] = useState('');
  const [timerDuration, setTimerDuration] = useState('');
  const [timerType, setTimerType] = useState<'timer' | 'alarm' | 'reminder'>('timer');
  const [timerScope, setTimerScope] = useState<'user' | 'household'>('user');
  const [selectedEmoji, setSelectedEmoji] = useState('');
  const [selectedSound, setSelectedSound] = useState('');
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [recurrence, setRecurrence] = useState('');
  const [targetDevices, setTargetDevices] = useState<string[]>([]);
  const [flashScreen, setFlashScreen] = useState(false);
  const [esphomeScript, setEsphomeScript] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [announcementDevice, setAnnouncementDevice] = useState('');
  const [announcementMessage, setAnnouncementMessage] = useState('');
  const [announcementVolume, setAnnouncementVolume] = useState(0.6);

  const { data: timers = EMPTY_TIMERS } = useQuery<TimerRecord[]>({
    queryKey: ['communication-timers'],
    queryFn: () => api.getTimers(),
    refetchInterval: 10000,
  });

  const { data: mediaTargets = EMPTY_DEVICES } = useQuery<DeviceAssignment[], Error, DeviceAssignment[]>({
    queryKey: ['devices'],
    queryFn: () => api.getDevices(),
    // One row per person who may use a speaker; list each speaker once.
    select: (data) => [
      ...new Map(data.filter((d) => d.device_id.startsWith('media_player.')).map((d) => [d.device_id, d])).values(),
    ],
  });

  const { data: timerDevicesResp } = useQuery<TimerDevicesResponse>({
    queryKey: ['communication-timer-devices'],
    queryFn: () => api.getTimerDevices(),
  });

  const availableTargets = useMemo<TimerTargetDevice[]>(() => {
    const map = new Map<string, TimerTargetDevice>();
    map.set('phone', {
      id: 'phone',
      name: 'Mobile App (Phone Alarm)',
      kind: 'mobile',
      has_speaker: true,
      has_screen: true,
    });
    for (const dev of timerDevicesResp?.devices ?? []) {
      map.set(dev.id, dev);
    }
    for (const d of mediaTargets) {
      if (!map.has(d.device_id)) {
        map.set(d.device_id, {
          id: d.device_id,
          name: deviceLabel(d.device_id),
          kind: 'ha_media_player',
          has_speaker: true,
          has_screen: false,
        });
      }
    }
    return Array.from(map.values());
  }, [timerDevicesResp, mediaTargets]);

  useEffect(() => {
    if (!announcementDevice && mediaTargets.length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAnnouncementDevice(mediaTargets[0].device_id);
    }
  }, [announcementDevice, mediaTargets]);

  const toggleTargetDevice = (id: string) => {
    setTargetDevices((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  };

  const createTimerMutation = useMutation({
    mutationFn: () => {
      const payload: Parameters<typeof api.createTimer>[0] = {
        title: timerTitle.trim(),
        duration_str: timerDuration.trim(),
      };
      if (timerType !== 'timer') {
        payload.type = timerType;
      }
      if (timerScope === 'household') {
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
      if (recurrence) {
        payload.recurrence = recurrence;
      }
      if (targetDevices.length === 1) {
        payload.target_device = targetDevices[0];
      } else if (targetDevices.length > 1) {
        payload.target_device = targetDevices[0];
        payload.target_devices = targetDevices;
      }
      if (flashScreen) {
        payload.flash_screen = true;
      }
      if (esphomeScript.trim()) {
        payload.esphome_script = esphomeScript.trim();
      }
      return api.createTimer(payload);
    },
    onSuccess: (res) => {
      // The handler answers 200 with FAILURE when it cannot read the time.
      if (res?.status && res.status !== 'SUCCESS') {
        toast.error(res.message || 'Could not set that timer');
        return;
      }
      queryClient.invalidateQueries({ queryKey: ['communication-timers'] });
      toast.success(timerType === 'alarm' ? 'Alarm created' : timerType === 'reminder' ? 'Reminder created' : 'Timer created');
      setTimerTitle('');
      setTimerDuration('');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to create timer'),
  });

  const deleteTimerMutation = useMutation({
    mutationFn: (timer: { title: string; type?: string; id?: string }) =>
      api.deleteTimer(
        timer.title,
        (timer.type === 'alarm' || timer.type === 'reminder') ? timer.type : 'timer',
        timer.id,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['communication-timers'] });
      toast.success('Timer removed');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to delete timer'),
  });

  const timerActionMutation = useMutation({
    mutationFn: (params: { action: 'pause' | 'resume' | 'stop' | 'reset'; timer?: TimerRecord }) =>
      api.timerAction(params.action, {
        title: params.timer?.title || '',
        type: (params.timer?.type === 'alarm' || params.timer?.type === 'reminder') ? params.timer.type : 'timer',
        id: params.timer?.id,
      }),
    onSuccess: (_res, vars) => {
      if (vars.action === 'stop') {
        stopLoopingAlarm();
        toast.success('Stopped ringing alarms & screen alerts');
      }
      queryClient.invalidateQueries({ queryKey: ['communication-timers'] });
    },
    onError: (error: Error) => toast.error(error.message || 'Timer action failed'),
  });

  const announcementMutation = useMutation({
    mutationFn: () => api.sendAnnouncement({
      entity_id: announcementDevice,
      message: announcementMessage,
      volume: announcementVolume,
    }),
    onSuccess: (res) => {
      if (res?.status && res.status !== 'SUCCESS') {
        toast.error(res.message || 'Announcement failed');
        return;
      }
      toast.success('Announcement sent');
      setAnnouncementMessage('');
    },
    onError: (error: Error) => toast.error(error.message || 'Announcement failed'),
  });

  return (
    <div className="space-y-5 md:space-y-6 pb-2">
      <header>
        <h2 className="text-xl font-black tracking-tighter text-white uppercase md:text-2xl">Timers &amp; announcements</h2>
        <p className="mt-2 text-sm text-slate-400">Timers, alarms, household reminders, and spoken announcements around the house.</p>
      </header>

      <div className="grid gap-6 xl:gap-8 lg:grid-cols-2">
        <section className="glass-panel p-4 sm:p-6">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <Clock3 size={20} className="shrink-0 text-orange-300" />
              <div className="min-w-0">
                <h3 className="text-xl font-bold text-white">Active Timers</h3>
                <p className="text-sm text-slate-400">Egg timers, alarms, and household reminders across ESPHome & mobile devices.</p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                onClick={() => timerActionMutation.mutate({ action: 'stop' })}
                className="flex items-center gap-1.5 rounded-xl border border-red-400/30 bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-200 hover:bg-red-500/20"
                title="Stop any ringing ESPHome speaker, flashing screen, or phone alarm"
              >
                <Square size={12} />
                Stop Alerts
              </button>
              <button
                type="button"
                onClick={() => setShowAdvanced((prev) => !prev)}
                aria-label="Configure alarm, sound, emoji and devices"
                className={`flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-semibold transition ${
                  showAdvanced
                    ? 'border-purple-400/50 bg-purple-500/20 text-purple-200'
                    : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/20'
                }`}
              >
                <SlidersHorizontal size={13} />
                Options
              </button>
            </div>
          </div>

          {/* Quick presets row */}
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            {QUICK_PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => {
                  setTimerDuration(preset.duration);
                  if (preset.emoji) setSelectedEmoji(preset.emoji);
                  if (preset.title && !timerTitle.trim()) setTimerTitle(preset.title);
                }}
                className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-slate-300 transition hover:border-purple-400/40 hover:bg-purple-500/15 hover:text-white"
              >
                {preset.label}
              </button>
            ))}
          </div>

          <div className="mb-4 flex flex-col sm:flex-row gap-3">
            <input
              type="text"
              value={timerTitle}
              onChange={(event) => setTimerTitle(event.target.value)}
              className="glass-input min-h-11 flex-1 min-w-0 text-base sm:text-sm"
              aria-label="Timer name"
              placeholder="Timer name"
            />
            <input
              type="text"
              value={timerDuration}
              onChange={(event) => setTimerDuration(event.target.value)}
              className="glass-input min-h-11 w-full sm:w-40 shrink-0 text-base sm:text-sm"
              placeholder="10m, 1h30m or 3pm"
              aria-label="Duration or time"
            />
            <button
              onClick={() => {
                if (!timerTitle.trim() || !timerDuration.trim()) {
                  toast.error('Enter a timer title and duration');
                  return;
                }
                unlockChime();
                createTimerMutation.mutate();
                trigger('success');
              }}
              disabled={createTimerMutation.isPending}
              className="glass-button min-h-11 shrink-0 px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              <Plus size={14} />
              Add Timer
            </button>
          </div>

          {showAdvanced && (
            <div className="mb-5 space-y-4 rounded-2xl border border-white/10 bg-black/25 p-4">
              {/* Type, Scope & Recurrence */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                  {(['timer', 'alarm', 'reminder'] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setTimerType(t)}
                      className={`rounded-lg px-3 py-1.5 text-xs font-semibold capitalize transition ${
                        timerType === t
                          ? 'bg-purple-500/30 text-purple-200 border border-purple-400/40'
                          : 'bg-white/5 text-slate-400 hover:text-white'
                      }`}
                    >
                      {t === 'timer' ? 'Egg / Minute Timer' : t}
                    </button>
                  ))}
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setTimerScope((prev) => (prev === 'household' ? 'user' : 'household'))}
                    className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                      timerScope === 'household'
                        ? 'bg-emerald-500/25 text-emerald-200 border border-emerald-400/40'
                        : 'bg-white/5 text-slate-400 hover:text-white'
                    }`}
                  >
                    <Users size={13} />
                    {timerScope === 'household' ? 'Household (Family)' : 'Personal (My Devices)'}
                  </button>

                  <select
                    aria-label="Alarm recurrence"
                    value={recurrence}
                    onChange={(e) => setRecurrence(e.target.value)}
                    className="glass-input px-2.5 py-1.5 text-xs bg-black/40"
                  >
                    <option value="">One-time</option>
                    <option value="daily">Daily</option>
                    <option value="weekdays">Weekdays (Mon-Fri)</option>
                    <option value="weekends">Weekends (Sat-Sun)</option>
                    <option value="weekly">Weekly</option>
                  </select>
                </div>
              </div>

              {/* Emoji Sound Picker */}
              <div>
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-300">Special Emoji Sounds</span>
                  {selectedEmoji && (
                    <button
                      type="button"
                      onClick={() => setSelectedEmoji('')}
                      className="text-xs text-slate-400 hover:text-white"
                    >
                      Clear {selectedEmoji}
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {EMOJI_PRESETS.map((item) => (
                    <button
                      key={item.emoji}
                      type="button"
                      onClick={() => {
                        const next = selectedEmoji === item.emoji ? '' : item.emoji;
                        setSelectedEmoji(next);
                        if (next) playTimerSound('emoji_sound', next);
                      }}
                      title={item.label}
                      className={`flex items-center gap-1 rounded-lg px-2 py-1 text-xs transition ${
                        selectedEmoji === item.emoji
                          ? 'bg-purple-500/30 text-white ring-1 ring-purple-400'
                          : 'bg-white/5 text-slate-300 hover:bg-white/10'
                      }`}
                    >
                      <span>{item.emoji}</span>
                      <span className="text-[11px] text-slate-400">{item.label}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Alarm Song / Noise & TTS */}
              <div>
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-300">Alarm Song / Sound & Spoken TTS</span>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setTtsEnabled((prev) => !prev)}
                      className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                        ttsEnabled
                          ? 'bg-cyan-500/20 text-cyan-200 border border-cyan-400/30'
                          : 'bg-white/5 text-slate-400'
                      }`}
                    >
                      <Volume2 size={13} />
                      {ttsEnabled ? 'Voice TTS On' : 'Voice TTS Off'}
                    </button>
                    <button
                      type="button"
                      onClick={() => playTimerSound(selectedSound || undefined, selectedEmoji || undefined)}
                      className="rounded-lg bg-purple-500/20 px-2.5 py-1 text-xs font-medium text-purple-200 hover:bg-purple-500/30"
                    >
                      Preview Sound
                    </button>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {ALARM_SOUNDS.map((snd) => (
                    <button
                      key={snd.id}
                      type="button"
                      onClick={() => {
                        const next = selectedSound === snd.id ? '' : snd.id;
                        setSelectedSound(next);
                        if (next) playTimerSound(next, selectedEmoji || undefined);
                      }}
                      title={snd.description}
                      className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                        selectedSound === snd.id
                          ? 'bg-purple-500/30 text-purple-200 border border-purple-400/40'
                          : 'bg-white/5 text-slate-400 hover:text-white'
                      }`}
                    >
                      {snd.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Multi-device assignment */}
              <div>
                <span className="mb-1.5 block text-xs font-semibold text-slate-300">
                  Target Devices (Assign to TVs, ESPHome devices, speakers, or mobile APK)
                </span>
                <div className="flex flex-wrap gap-2">
                  {availableTargets.map((dev) => {
                    const selected = targetDevices.includes(dev.id);
                    const isTv = dev.kind === 'tv' || ['roku', 'webos', 'samsung', 'android_tv', 'cast', 'bravia'].includes(dev.platform || '');
                    const platformBadge = dev.platform && dev.platform !== 'unknown' && dev.platform !== 'apk'
                      ? dev.platform.replace(/_/g, ' ').toUpperCase()
                      : null;
                    return (
                      <button
                        key={dev.id}
                        type="button"
                        onClick={() => toggleTargetDevice(dev.id)}
                        title={dev.os_version ? `${dev.name} (${dev.os_version})` : dev.name}
                        className={`flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-medium transition ${
                          selected
                            ? 'bg-purple-500/30 text-purple-100 border border-purple-400/50'
                            : 'bg-white/5 text-slate-300 border border-white/5 hover:border-white/20'
                        }`}
                      >
                        {dev.kind === 'mobile' || dev.kind === 'phone' ? (
                          <Smartphone size={13} className="text-cyan-300" />
                        ) : isTv ? (
                          <Monitor size={13} className="text-sky-300" />
                        ) : !dev.has_speaker && dev.has_screen ? (
                          <Monitor size={13} className="text-amber-300" />
                        ) : (
                          <BellRing size={13} className="text-purple-300" />
                        )}
                        <span>{dev.name}</span>
                        {platformBadge && (
                          <span className="rounded bg-sky-500/20 px-1.5 py-0.5 text-[10px] text-sky-200">
                            {platformBadge}
                          </span>
                        )}
                        {dev.intercom_mode === 'twoway' && (
                          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-200">
                            2-Way Mic
                          </span>
                        )}
                        {!dev.has_speaker && dev.has_screen && (
                          <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-200">
                            Screen Only
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Screen Flash & ESPHome Script */}
              <div className="rounded-xl border border-amber-400/20 bg-amber-500/5 p-3 space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 text-xs font-semibold text-amber-200">
                    <Zap size={14} />
                    ESPHome Onscreen Flash & Custom Action Script
                  </span>
                  <button
                    type="button"
                    onClick={() => setFlashScreen((prev) => !prev)}
                    className={`rounded-lg px-2.5 py-1 text-xs font-semibold transition ${
                      flashScreen
                        ? 'bg-amber-400/30 text-amber-100 border border-amber-300/50'
                        : 'bg-white/5 text-slate-300'
                    }`}
                  >
                    {flashScreen ? 'Flash Screen Until Button Pressed: ON' : 'Auto Flash Speakerless Screens'}
                  </button>
                </div>
                <input
                  type="text"
                  value={esphomeScript}
                  onChange={(e) => setEsphomeScript(e.target.value)}
                  placeholder="Optional ESPHome script/service (e.g. flash_screen, alarm_flash, set_glance)"
                  aria-label="ESPHome script"
                  className="glass-input min-h-9 w-full text-xs"
                />
              </div>
            </div>
          )}

          <div className="space-y-3">
            {timers.map((timer) => {
              const paused = !timer.active && Boolean(timer.paused_at);
              const devices = timer.target_devices?.length
                ? timer.target_devices
                : (timer.target_device ? [timer.target_device] : []);
              return (
                <div key={timer.id} className="glass-card flex items-center justify-between gap-3 p-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {timer.emoji && <span className="text-base">{timer.emoji}</span>}
                      {timer.type === 'alarm' && !timer.emoji && (
                        <AlarmClock size={15} className="text-amber-300 shrink-0" />
                      )}
                      <p className="font-semibold text-white truncate">{timer.title}</p>
                      {timer.scope === 'household' && (
                        <span className="rounded-md bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
                          Household
                        </span>
                      )}
                      {timer.recurrence && (
                        <span className="rounded-md bg-purple-500/15 px-2 py-0.5 text-[10px] font-medium text-purple-300">
                          {timer.recurrence}
                        </span>
                      )}
                      {paused && (
                        <span className="rounded-md bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                          Paused
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-slate-400">
                      {new Date(timer.expires_at).toLocaleString()}
                      {devices.length > 0 && ` • ${devices.map(deviceLabel).join(', ')}`}
                      {timer.sound && ` • 🎵 ${timer.sound.replace(/_/g, ' ')}`}
                      {timer.flash_screen && ' • ⚡ Screen Flash'}
                    </p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      type="button"
                      onClick={() =>
                        timerActionMutation.mutate({
                          action: paused ? 'resume' : 'pause',
                          timer,
                        })
                      }
                      className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-400 transition hover:bg-white/10 hover:text-purple-300"
                      aria-label={`${paused ? 'Resume' : 'Pause'} ${timer.title}`}
                      title={paused ? 'Resume' : 'Pause'}
                    >
                      {paused ? <Play size={15} /> : <Pause size={15} />}
                    </button>
                    {timer.duration_sec && (
                      <button
                        type="button"
                        onClick={() => timerActionMutation.mutate({ action: 'reset', timer })}
                        className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-400 transition hover:bg-white/10 hover:text-cyan-300"
                        aria-label={`Reset ${timer.title}`}
                        title="Reset"
                      >
                        <RotateCcw size={15} />
                      </button>
                    )}
                    <button
                      onClick={() =>
                        deleteTimerMutation.mutate({
                          title: timer.title,
                          type: timer.type,
                          id: timer.id,
                        })
                      }
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                      aria-label={`Delete ${timer.title}`}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              );
            })}
            {!timers.length && (
              <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
                No active timers.
              </p>
            )}
          </div>
        </section>

        <section className="glass-panel p-4 sm:p-6">
          <div className="mb-6 flex items-center gap-3">
            <Megaphone size={20} className="shrink-0 text-indigo-300" />
            <div>
              <h3 className="text-xl font-bold text-white">Announcements</h3>
              <p className="text-sm text-slate-400">Jarvis says it out loud on a speaker.</p>
            </div>
          </div>

          <div className="space-y-4">
            <select
              aria-label="Announcement target device"
              value={announcementDevice}
              onChange={(event) => setAnnouncementDevice(event.target.value)}
              className="glass-input min-h-11 w-full bg-black/30"
            >
              <option value="">{mediaTargets.length ? 'Select a speaker' : 'No speakers assigned to you'}</option>
              {mediaTargets.map((device) => (
                <option key={device.device_id} value={device.device_id}>
                  {deviceLabel(device.device_id)}
                </option>
              ))}
            </select>
            <textarea
              value={announcementMessage}
              onChange={(event) => setAnnouncementMessage(event.target.value)}
              className="glass-input min-h-28 w-full text-base sm:text-sm"
              placeholder="What should Jarvis say?"
              aria-label="Announcement message"
            />
            <label className="block text-sm text-slate-400">
              Volume: {Math.round(announcementVolume * 100)}%
              <input
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={announcementVolume}
                onChange={(event) => setAnnouncementVolume(Number(event.target.value))}
                className="mt-2 w-full"
              />
            </label>
            <button
              onClick={() => {
                if (!announcementDevice) {
                  toast.error('Select a target device');
                  return;
                }
                if (!announcementMessage.trim()) {
                  toast.error('Type what Jarvis should say');
                  return;
                }
                announcementMutation.mutate();
                trigger('success');
              }}
              disabled={announcementMutation.isPending}
              className="glass-button min-h-11 w-full px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              <BellRing size={14} />
              Send Announcement
            </button>
          </div>
        </section>
      </div>

    </div>
  );
};

export default AnnouncementsPanel;

