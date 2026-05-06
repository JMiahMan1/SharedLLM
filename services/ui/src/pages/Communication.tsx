import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BellRing,
  Calendar,
  Clock3,
  FileText,
  Megaphone,
  Plus,
  Trash2,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { DeviceAssignment, ExecutionResponse, TimerRecord } from '../services/api';

const Communication = () => {
  const queryClient = useQueryClient();
  const [timerTitle, setTimerTitle] = useState('');
  const [timerDuration, setTimerDuration] = useState('');
  const [announcementDevice, setAnnouncementDevice] = useState('');
  const [announcementMessage, setAnnouncementMessage] = useState('');
  const [announcementVolume, setAnnouncementVolume] = useState(0.6);
  const [eventSummary, setEventSummary] = useState('');
  const [eventStartTime, setEventStartTime] = useState('');
  const [noteTitle, setNoteTitle] = useState('');
  const [noteContent, setNoteContent] = useState('');
  const [noteResult, setNoteResult] = useState<ExecutionResponse | null>(null);

  const { data: timers = [] } = useQuery<TimerRecord[]>({
    queryKey: ['communication-timers'],
    queryFn: () => api.getTimers(),
    refetchInterval: 10000,
  });

  const { data: devices = [] } = useQuery<DeviceAssignment[]>({
    queryKey: ['devices'],
    queryFn: () => api.getDevices(),
  });

  const { data: calendarList } = useQuery<ExecutionResponse>({
    queryKey: ['calendar-list'],
    queryFn: () => api.getCalendarList(),
  });

  const { data: calendarEvents, refetch: refetchCalendarEvents } = useQuery<ExecutionResponse>({
    queryKey: ['calendar-events'],
    queryFn: () => api.getCalendarEvents(),
  });

  const mediaTargets = useMemo(
    () => devices.filter((device) => device.device_id.startsWith('media_player.')),
    [devices],
  );

  useEffect(() => {
    if (!announcementDevice && mediaTargets.length > 0) {
      setAnnouncementDevice(mediaTargets[0].device_id);
    }
  }, [announcementDevice, mediaTargets]);

  const createTimerMutation = useMutation({
    mutationFn: () => api.createTimer({ title: timerTitle, duration_str: timerDuration }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['communication-timers'] });
      toast.success('Timer created');
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
    onSuccess: () => toast.success('Announcement sent'),
    onError: (error: Error) => toast.error(error.message || 'Announcement failed'),
  });

  const calendarMutation = useMutation({
    mutationFn: () => api.addCalendarEvent({ summary: eventSummary, start_time: eventStartTime }),
    onSuccess: () => {
      refetchCalendarEvents();
      toast.success('Calendar event added');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to add event'),
  });

  const noteMutation = useMutation({
    mutationFn: async (action: 'create' | 'read' | 'append' | 'delete') => {
      if (action === 'create') {
        return api.createNote({ title: noteTitle, content: noteContent, category: 'Shared' });
      }
      if (action === 'read') {
        return api.readNote(noteTitle);
      }
      if (action === 'append') {
        return api.appendNote({ title: noteTitle, content: noteContent });
      }
      return api.deleteNote(noteTitle);
    },
    onSuccess: (data) => {
      setNoteResult(data);
      toast.success('Note action completed');
    },
    onError: (error: Error) => toast.error(error.message || 'Note action failed'),
  });

  return (
    <div className="space-y-8 pb-12">
      <header>
        <h2 className="text-4xl font-black tracking-tighter text-white uppercase">Communication</h2>
        <p className="mt-2 text-slate-400">Live execution-backed timers, announcements, calendars, and notes.</p>
      </header>

      <div className="grid gap-8 xl:grid-cols-2">
        <section className="glass-panel p-6">
          <div className="mb-6 flex items-center gap-3">
            <Clock3 size={20} className="text-orange-300" />
            <div>
              <h3 className="text-xl font-bold text-white">Active Timers</h3>
              <p className="text-sm text-slate-400">Current live timer state from Redis-backed execution.</p>
            </div>
          </div>

          <div className="mb-4 grid gap-3 md:grid-cols-[1fr_140px_auto]">
            <input
              type="text"
              value={timerTitle}
              onChange={(event) => setTimerTitle(event.target.value)}
              className="glass-input"
              placeholder="Timer name"
            />
            <input
              type="text"
              value={timerDuration}
              onChange={(event) => setTimerDuration(event.target.value)}
              className="glass-input"
              placeholder="Duration or time expression"
            />
            <button
              onClick={() => {
                if (!timerTitle.trim() || !timerDuration.trim()) {
                  toast.error('Enter a timer title and duration');
                  return;
                }
                createTimerMutation.mutate();
              }}
              className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              <Plus size={14} />
              Add Timer
            </button>
          </div>

          <div className="space-y-3">
            {timers.map((timer) => (
              <div key={timer.id} className="glass-card flex items-center justify-between p-4">
                <div>
                  <p className="font-semibold text-white">{timer.title}</p>
                  <p className="mt-1 text-xs text-slate-400">{new Date(timer.expires_at).toLocaleString()}</p>
                </div>
                <button
                  onClick={() => deleteTimerMutation.mutate(timer.title)}
                  className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300"
                  aria-label={`Delete ${timer.title}`}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
            {!timers.length && (
              <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
                No active timers found.
              </p>
            )}
          </div>
        </section>

        <section className="glass-panel p-6">
          <div className="mb-6 flex items-center gap-3">
            <Megaphone size={20} className="text-indigo-300" />
            <div>
              <h3 className="text-xl font-bold text-white">Announcements</h3>
              <p className="text-sm text-slate-400">Send real TTS announcements to assigned media devices.</p>
            </div>
          </div>

          <div className="space-y-4">
            <select
              value={announcementDevice}
              onChange={(event) => setAnnouncementDevice(event.target.value)}
              className="glass-input w-full bg-black/30"
            >
              <option value="">Select target device</option>
              {mediaTargets.map((device) => (
                <option key={device.device_id} value={device.device_id}>
                  {device.device_id}
                </option>
              ))}
            </select>
            <textarea
              value={announcementMessage}
              onChange={(event) => setAnnouncementMessage(event.target.value)}
              className="glass-input min-h-28 w-full"
              placeholder="Enter the announcement message"
            />
            <label className="block text-sm text-slate-400">
              Volume: {announcementVolume.toFixed(1)}
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
                announcementMutation.mutate();
              }}
              className="glass-button w-full px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              <BellRing size={14} />
              Send Announcement
            </button>
          </div>
        </section>
      </div>

      <div className="grid gap-8 xl:grid-cols-2">
        <section className="glass-panel p-6">
          <div className="mb-6 flex items-center gap-3">
            <Calendar size={20} className="text-emerald-300" />
            <div>
              <h3 className="text-xl font-bold text-white">Calendar</h3>
              <p className="text-sm text-slate-400">Live calendar listing, event readout, and event creation.</p>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-[1fr_180px_auto]">
            <input
              type="text"
              value={eventSummary}
              onChange={(event) => setEventSummary(event.target.value)}
              className="glass-input"
              placeholder="Calendar event title"
            />
            <input
              type="text"
              value={eventStartTime}
              onChange={(event) => setEventStartTime(event.target.value)}
              className="glass-input"
              placeholder="Start time"
            />
            <button
              onClick={() => {
                if (!eventSummary.trim() || !eventStartTime.trim()) {
                  toast.error('Enter an event summary and time');
                  return;
                }
                calendarMutation.mutate();
              }}
              className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
            >
              <Plus size={14} />
              Add Event
            </button>
          </div>

          <div className="mt-6 space-y-4">
            <div className="rounded-2xl border border-white/5 bg-black/20 p-4">
              <p className="mb-2 text-xs font-black uppercase tracking-widest text-slate-500">Calendars</p>
              <pre className="whitespace-pre-wrap text-sm text-slate-300">{calendarList?.message || 'Loading calendars...'}</pre>
            </div>
            <div className="rounded-2xl border border-white/5 bg-black/20 p-4">
              <p className="mb-2 text-xs font-black uppercase tracking-widest text-slate-500">Upcoming Events</p>
              <pre className="whitespace-pre-wrap text-sm text-slate-300">{calendarEvents?.message || 'Loading events...'}</pre>
            </div>
          </div>
        </section>

        <section className="glass-panel p-6">
          <div className="mb-6 flex items-center gap-3">
            <FileText size={20} className="text-cyan-300" />
            <div>
              <h3 className="text-xl font-bold text-white">Notes</h3>
              <p className="text-sm text-slate-400">Create, read, append, and delete shared notes through Nextcloud.</p>
            </div>
          </div>

          <div className="space-y-4">
            <input
              type="text"
              value={noteTitle}
              onChange={(event) => setNoteTitle(event.target.value)}
              className="glass-input w-full"
              placeholder="Note title"
            />
            <textarea
              value={noteContent}
              onChange={(event) => setNoteContent(event.target.value)}
              className="glass-input min-h-28 w-full"
              placeholder="Note content"
            />
            <div className="grid gap-3 md:grid-cols-4">
              <button onClick={() => noteMutation.mutate('create')} className="glass-button px-3 py-3 text-[10px] font-black uppercase tracking-widest">Create</button>
              <button onClick={() => noteMutation.mutate('read')} className="glass-button px-3 py-3 text-[10px] font-black uppercase tracking-widest">Read</button>
              <button onClick={() => noteMutation.mutate('append')} className="glass-button px-3 py-3 text-[10px] font-black uppercase tracking-widest">Append</button>
              <button onClick={() => noteMutation.mutate('delete')} className="glass-button px-3 py-3 text-[10px] font-black uppercase tracking-widest">Delete</button>
            </div>
            <div className="rounded-2xl border border-white/5 bg-black/20 p-4">
              <p className="mb-2 text-xs font-black uppercase tracking-widest text-slate-500">Last Note Response</p>
              <pre className="whitespace-pre-wrap text-sm text-slate-300">{noteResult?.message || 'Run a note action to see the live response.'}</pre>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};

export default Communication;
