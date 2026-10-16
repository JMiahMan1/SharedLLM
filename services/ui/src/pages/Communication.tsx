import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BellRing, Calendar, ChevronRight, Clock3, Megaphone, MessageSquare, Plus, StickyNote, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { Link as RouterLink } from 'react-router-dom';
import { api } from '../services/api';
import { useHaptics } from '../hooks/useHaptics';
import type { DeviceAssignment, TimerRecord } from '../services/api';

const EMPTY_TIMERS: TimerRecord[] = [];
const EMPTY_DEVICES: DeviceAssignment[] = [];

/** Chat, the calendar and notes each have one home; this page points there. */
const ELSEWHERE = [
  { icon: MessageSquare, tone: 'text-fuchsia-300', title: 'Chat', detail: 'Conversations, voice messages and games live on the Family page.', to: '/family', cta: 'Open Family' },
  { icon: Calendar, tone: 'text-emerald-300', title: 'Calendar', detail: 'Every family calendar in one agenda, with add, edit and delete.', to: '/calendar', cta: 'Open Calendar' },
  { icon: StickyNote, tone: 'text-cyan-300', title: 'Notes', detail: 'Notes and checklists, kept in Nextcloud and searchable by Jarvis.', to: '/notes', cta: 'Open Notes' },
];

/** "media_player.kitchen_speaker" -> "Kitchen speaker". */
function deviceLabel(deviceId: string): string {
  const name = deviceId.replace(/^media_player\./, '').replace(/_/g, ' ').trim();
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : deviceId;
}

/**
 * Timers and spoken announcements, plus the way to the family's chat,
 * calendar and notes. Those used to have second, older copies here (a notes
 * editor whose Save appended the whole note to itself, a calendar that only
 * printed "Loaded N events"); there is one implementation of each now.
 */
const Communication = () => {
  const queryClient = useQueryClient();
  const { trigger } = useHaptics();
  const [timerTitle, setTimerTitle] = useState('');
  const [timerDuration, setTimerDuration] = useState('');
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
    select: (data) => data.filter((device) => device.device_id.startsWith('media_player.')),
  });

  useEffect(() => {
    if (!announcementDevice && mediaTargets.length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setAnnouncementDevice(mediaTargets[0].device_id);
    }
  }, [announcementDevice, mediaTargets]);

  const createTimerMutation = useMutation({
    mutationFn: () => api.createTimer({ title: timerTitle, duration_str: timerDuration }),
    onSuccess: (res) => {
      // The handler answers 200 with FAILURE when it cannot read the time.
      if (res?.status && res.status !== 'SUCCESS') {
        toast.error(res.message || 'Could not set that timer');
        return;
      }
      queryClient.invalidateQueries({ queryKey: ['communication-timers'] });
      toast.success('Timer created');
      setTimerTitle('');
      setTimerDuration('');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to create timer'),
  });

  const deleteTimerMutation = useMutation({
    mutationFn: (title: string) => api.deleteTimer(title),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['communication-timers'] });
      toast.success('Timer removed');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to delete timer'),
  });

  const announcementMutation = useMutation({
    mutationFn: () => api.sendAnnouncement({
      entity_id: announcementDevice,
      message: announcementMessage,
      volume: announcementVolume,
    }),
    onSuccess: () => {
      toast.success('Announcement sent');
      setAnnouncementMessage('');
    },
    onError: (error: Error) => toast.error(error.message || 'Announcement failed'),
  });

  return (
    <div className="space-y-6 md:space-y-8 pb-12">
      <header>
        <h2 className="text-2xl md:text-4xl font-black tracking-tighter text-white uppercase">Communication</h2>
        <p className="mt-2 text-sm md:text-base text-slate-400">Timers and spoken announcements around the house.</p>
      </header>

      <div className="grid gap-6 xl:gap-8 lg:grid-cols-2">
        <section className="glass-panel p-4 sm:p-6">
          <div className="mb-6 flex items-center gap-3">
            <Clock3 size={20} className="shrink-0 text-orange-300" />
            <div>
              <h3 className="text-xl font-bold text-white">Active Timers</h3>
              <p className="text-sm text-slate-400">Jarvis notifies your phone when one is up.</p>
            </div>
          </div>

          <div className="mb-4 flex flex-col sm:flex-row gap-3">
            <input
              type="text"
              value={timerTitle}
              onChange={(event) => setTimerTitle(event.target.value)}
              className="glass-input min-h-11 flex-1 min-w-0 text-base sm:text-sm"
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

          <div className="space-y-3">
            {timers.map((timer) => (
              <div key={timer.id} className="glass-card flex items-center justify-between p-4">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-white truncate">{timer.title}</p>
                  <p className="mt-1 text-xs text-slate-400">{new Date(timer.expires_at).toLocaleString()}</p>
                </div>
                <button
                  onClick={() => deleteTimerMutation.mutate(timer.title)}
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                  aria-label={`Delete ${timer.title}`}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
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

      <nav className="grid gap-3 md:grid-cols-3" aria-label="Chat, calendar and notes">
        {ELSEWHERE.map(({ icon: Icon, tone, title, detail, to, cta }) => (
          <RouterLink
            key={to}
            to={to}
            aria-label={cta}
            className="glass-panel flex min-h-16 items-center gap-3 rounded-2xl border border-white/10 p-4 hover:border-purple-400/40"
          >
            <Icon size={20} className={`shrink-0 ${tone}`} />
            <span className="min-w-0 flex-1">
              <span className="block font-bold text-white">{title}</span>
              <span className="block text-xs text-slate-400">{detail}</span>
            </span>
            <ChevronRight size={16} className="shrink-0 text-slate-500" />
          </RouterLink>
        ))}
      </nav>
    </div>
  );
};

export default Communication;
