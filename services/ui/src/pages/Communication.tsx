import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BellRing,
  Calendar,
  Clock3,
  FileText,
  Megaphone,
  MessageSquare,
  Mic,
  MicOff,
  Plus,
  Send,
  Trash2,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type {
  DeviceAssignment,
  ExecutionResponse,
  TalkConversation,
  TalkMessage,
  TimerRecord,
} from '../services/api';

const detailList = <T,>(response: ExecutionResponse | undefined, key: string): T[] => {
  const detail = response?.detail as Record<string, unknown> | null | undefined;
  const value = detail?.[key];
  return Array.isArray(value) ? (value as T[]) : [];
};

const blobToDataUrl = (blob: Blob) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('Failed to read audio recording.'));
    reader.readAsDataURL(blob);
  });

const Communication = () => {
  const queryClient = useQueryClient();
  const [timerTitle, setTimerTitle] = useState('');
  const [timerDuration, setTimerDuration] = useState('');
  const [announcementDevice, setAnnouncementDevice] = useState('');
  const [announcementMessage, setAnnouncementMessage] = useState('');
  const [announcementVolume, setAnnouncementVolume] = useState(0.6);
  const [noteTitle, setNoteTitle] = useState('');
  const [noteContent, setNoteContent] = useState('');
  const [noteResult, setNoteResult] = useState<ExecutionResponse | null>(null);
  const [talkTargetUser, setTalkTargetUser] = useState('');
  const [selectedTalkToken, setSelectedTalkToken] = useState('');
  const [talkMessage, setTalkMessage] = useState('');
  const [voiceCaption, setVoiceCaption] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [recordedAudio, setRecordedAudio] = useState<{ base64: string; mimeType: string; fileName: string } | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recordedChunksRef = useRef<Blob[]>([]);

  const [selectedCalendar, setSelectedCalendar] = useState('');
  const [eventForm, setEventForm] = useState({
    summary: '',
    description: '',
    start: '',
    end: '',
  });

  const { data: timers = [] } = useQuery<TimerRecord[]>({
    queryKey: ['communication-timers'],
    queryFn: () => api.getTimers(),
    refetchInterval: 10000,
  });

  const { data: mediaTargets = [] } = useQuery<DeviceAssignment[], Error, DeviceAssignment[]>({
    queryKey: ['devices'],
    queryFn: () => api.getDevices(),
    select: (data) => data.filter((device) => device.device_id.startsWith('media_player.')),
  });

  const { data: calendars = [] } = useQuery<ExecutionResponse, Error, { id: string; display_name: string }[]>({
    queryKey: ['calendar-list'],
    queryFn: () => api.getCalendarList(),
    select: (response) => detailList<{ id: string; display_name: string }>(response, 'calendars'),
  });

  const { data: calendarEvents, refetch: refetchCalendarEvents } = useQuery<ExecutionResponse>({
    queryKey: ['calendar-events', selectedCalendar],
    queryFn: () => api.getCalendarEvents(selectedCalendar),
  });

  const { data: talkConversations = [] } = useQuery<ExecutionResponse, Error, TalkConversation[]>({
    queryKey: ['talk-conversations'],
    queryFn: () => api.getTalkConversations(),
    refetchInterval: 15000,
    select: (response) => detailList<TalkConversation>(response, 'conversations'),
  });

  const { data: talkMessages = [], refetch: refetchTalkMessages } = useQuery<ExecutionResponse, Error, TalkMessage[]>({
    queryKey: ['talk-messages', selectedTalkToken],
    queryFn: () => api.getTalkMessages(selectedTalkToken),
    enabled: Boolean(selectedTalkToken),
    refetchInterval: selectedTalkToken ? 8000 : false,
    select: (response) => detailList<TalkMessage>(response, 'messages'),
  });

  useEffect(() => {
    if (calendars.length > 0 && !selectedCalendar) {
      setSelectedCalendar(calendars[0].id);
    }
  }, [calendars, selectedCalendar]);

  useEffect(() => {
    if (!announcementDevice && mediaTargets.length > 0) {
      setAnnouncementDevice(mediaTargets[0].device_id);
    }
  }, [announcementDevice, mediaTargets]);

  useEffect(() => {
    if (!selectedTalkToken && talkConversations.length > 0) {
      setSelectedTalkToken(talkConversations[0].token);
    }
  }, [selectedTalkToken, talkConversations]);

  const createTimerMutation = useMutation({
    mutationFn: () => api.createTimer({ title: timerTitle, duration_str: timerDuration }),
    onSuccess: () => {
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

  const calendarMutation = useMutation({
    mutationFn: () => api.addCalendarEvent({ 
      summary: eventForm.summary, 
      start_time: eventForm.start, 
      calendar_name: selectedCalendar 
    }),
    onSuccess: () => {
      refetchCalendarEvents();
      toast.success('Calendar event added');
      setEventForm({ ...eventForm, summary: '', start: '' });
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

  const openTalkConversationMutation = useMutation({
    mutationFn: (payload: { token?: string; target_user?: string }) => api.openTalkConversation(payload),
    onSuccess: (data) => {
      const detail = data.detail as { conversation?: TalkConversation } | undefined;
      const token = detail?.conversation?.token;
      queryClient.invalidateQueries({ queryKey: ['talk-conversations'] });
      if (token) {
        setSelectedTalkToken(token);
      }
      setTalkTargetUser('');
      toast.success('Conversation ready');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to open conversation'),
  });

  const talkMessageMutation = useMutation({
    mutationFn: () => api.sendTalkMessage({ token: selectedTalkToken, message: talkMessage }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['talk-conversations'] }),
        refetchTalkMessages(),
      ]);
      setTalkMessage('');
      toast.success('Talk message sent');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to send Talk message'),
  });

  const talkVoiceMutation = useMutation({
    mutationFn: () => {
      if (!recordedAudio) {
        throw new Error('Record audio before sending.');
      }
      return api.sendTalkVoice({
        token: selectedTalkToken,
        audio_base64: recordedAudio.base64,
        mime_type: recordedAudio.mimeType,
        file_name: recordedAudio.fileName,
        caption: voiceCaption,
      });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['talk-conversations'] }),
        refetchTalkMessages(),
      ]);
      setRecordedAudio(null);
      setVoiceCaption('');
      toast.success('Voice message sent');
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to send voice message'),
  });

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      recordedChunksRef.current = [];
      const recorder = new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          recordedChunksRef.current.push(event.data);
        }
      };
      recorder.onstop = async () => {
        const type = recordedChunksRef.current[0]?.type || 'audio/webm';
        const blob = new Blob(recordedChunksRef.current, { type });
        const base64 = await blobToDataUrl(blob);
        setRecordedAudio({
          base64,
          mimeType: type,
          fileName: `talk-voice-${Date.now()}.${type.includes('mp4') ? 'm4a' : 'webm'}`,
        });
        mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
        mediaStreamRef.current = null;
      };
      recorder.start();
      setIsRecording(true);
      toast.success('Recording started');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Microphone access failed');
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current = null;
    setIsRecording(false);
  };

  return (
    <div className="space-y-8 pb-12">
      <header>
        <h2 className="text-4xl font-black tracking-tighter text-white uppercase">Communication</h2>
        <p className="mt-2 text-slate-400">Live execution-backed timers, announcements, Nextcloud Talk chat, calendars, and notes.</p>
      </header>

      <div className="grid gap-6 xl:gap-8 lg:grid-cols-2">
        <section className="glass-panel p-6">
          <div className="mb-6 flex items-center gap-3">
            <Clock3 size={20} className="text-orange-300" />
            <div>
              <h3 className="text-xl font-bold text-white">Active Timers</h3>
              <p className="text-sm text-slate-400">Current live timer state from Redis-backed execution.</p>
            </div>
          </div>

          <div className="mb-4 grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-[1fr_140px_auto]">
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
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-white truncate">{timer.title}</p>
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
              aria-label="Announcement target device"
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

      <section className="glass-panel p-6">
        <div className="mb-6 flex items-center gap-3">
          <MessageSquare size={20} className="text-fuchsia-300" />
          <div>
            <h3 className="text-xl font-bold text-white">Nextcloud Talk</h3>
            <p className="text-sm text-slate-400">Open live conversations, chat with users, and record voice messages into Talk.</p>
          </div>
        </div>

        <div className="mb-5 grid gap-3 grid-cols-1 md:grid-cols-[1fr_auto]">
          <input
            type="text"
            value={talkTargetUser}
            onChange={(event) => setTalkTargetUser(event.target.value)}
            className="glass-input"
            placeholder="Nextcloud username to open"
          />
          <button
            onClick={() => {
              if (!talkTargetUser.trim()) {
                toast.error('Enter a Nextcloud username');
                return;
              }
              openTalkConversationMutation.mutate({ target_user: talkTargetUser.trim() });
            }}
            className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
          >
            <Plus size={14} />
            Open Conversation
          </button>
        </div>

        <div className="grid gap-6 grid-cols-1 xl:grid-cols-[300px_1fr]">
          <div className="space-y-3">
            {talkConversations.map((conversation) => (
              <button
                key={conversation.token}
                onClick={() => setSelectedTalkToken(conversation.token)}
                className={`w-full rounded-2xl border p-4 text-left transition ${
                  selectedTalkToken === conversation.token
                    ? 'border-fuchsia-400/40 bg-fuchsia-500/10'
                    : 'border-white/5 bg-white/5 hover:border-white/10 hover:bg-white/10'
                }`}
              >
                <p className="font-semibold text-white truncate">{conversation.display_name}</p>
                <p className="mt-1 text-xs text-slate-400 truncate">{conversation.description || conversation.token}</p>
                <p className="mt-2 text-xs text-slate-500 truncate">{conversation.last_message || 'No messages yet.'}</p>
              </button>
            ))}
            {!talkConversations.length && (
              <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
                No Talk conversations available.
              </p>
            )}
          </div>

          <div className="space-y-4">
            <div className="rounded-2xl border border-white/5 bg-black/20 p-4">
              <p className="mb-3 text-xs font-black uppercase tracking-widest text-slate-500">Conversation Feed</p>
              <div className="space-y-3">
                {talkMessages.map((message) => (
                  <div key={`${message.id ?? message.timestamp}-${message.actor_display_name}`} className="rounded-2xl border border-white/5 bg-white/5 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-semibold text-white">{message.actor_display_name}</p>
                      <p className="text-[11px] text-slate-500">
                        {message.timestamp ? new Date(message.timestamp * 1000).toLocaleString() : 'Pending'}
                      </p>
                    </div>
                    <p className="mt-2 text-sm text-slate-300 break-words">{message.message || message.system_message || 'Empty message'}</p>
                  </div>
                ))}
                {selectedTalkToken && !talkMessages.length && (
                  <p className="text-sm text-slate-500">No messages yet for this conversation.</p>
                )}
                {!selectedTalkToken && (
                  <p className="text-sm text-slate-500">Select or open a conversation to load messages.</p>
                )}
              </div>
            </div>

            <div className="grid gap-3 grid-cols-1 md:grid-cols-[1fr_auto]">
              <textarea
                value={talkMessage}
                onChange={(event) => setTalkMessage(event.target.value)}
                className="glass-input min-h-28 w-full"
                placeholder="Send a live Nextcloud Talk message"
              />
              <button
                onClick={() => {
                  if (!selectedTalkToken) {
                    toast.error('Open or select a conversation first');
                    return;
                  }
                  if (!talkMessage.trim()) {
                    toast.error('Enter a message');
                    return;
                  }
                  talkMessageMutation.mutate();
                }}
                className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest md:self-start"
              >
                <Send size={14} />
                Send Message
              </button>
            </div>

            <div className="rounded-2xl border border-white/5 bg-black/20 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-white">Voice Message</p>
                  <p className="text-xs text-slate-400">Record in the browser and send the clip into the active Talk conversation.</p>
                </div>
                <button
                  onClick={() => {
                    if (!selectedTalkToken) {
                      toast.error('Open or select a conversation first');
                      return;
                    }
                    if (isRecording) {
                      stopRecording();
                    } else {
                      void startRecording();
                    }
                  }}
                  className={`glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest ${
                    isRecording ? 'border-red-400/30 bg-red-500/10 text-red-200' : ''
                  }`}
                >
                  {isRecording ? <MicOff size={14} /> : <Mic size={14} />}
                  {isRecording ? 'Stop Recording' : 'Record Voice'}
                </button>
              </div>
              <div className="mt-4 grid gap-3 grid-cols-1 md:grid-cols-[1fr_auto]">
                <input
                  type="text"
                  value={voiceCaption}
                  onChange={(event) => setVoiceCaption(event.target.value)}
                  className="glass-input"
                  placeholder="Optional caption for voice message"
                />
                <button
                  onClick={() => {
                    if (!selectedTalkToken) {
                      toast.error('Open or select a conversation first');
                      return;
                    }
                    if (!recordedAudio) {
                      toast.error('Record audio before sending');
                      return;
                    }
                    talkVoiceMutation.mutate();
                  }}
                  className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest"
                >
                  <Send size={14} />
                  Send Voice
                </button>
              </div>
              {recordedAudio && (
                <div className="mt-4 rounded-xl border border-white/5 bg-white/5 p-3">
                  <p className="mb-2 text-[10px] font-black uppercase tracking-widest text-slate-500">Recorded Preview</p>
                  <audio controls src={recordedAudio.base64} className="w-full h-10" />
                </div>
              )}
              <p className="mt-3 text-xs text-slate-500">
                {recordedAudio ? `Recorded clip ready: ${recordedAudio.fileName}` : 'No recorded clip yet.'}
              </p>
            </div>
          </div>
        </div>
      </section>

      <div className="grid gap-6 xl:gap-8 lg:grid-cols-2">
        <section className="glass-panel p-6">
          <div className="mb-6 flex items-center gap-3">
            <Calendar size={20} className="text-emerald-300" />
            <div>
              <h3 className="text-xl font-bold text-white">Calendar</h3>
              <p className="text-sm text-slate-400">Live calendar listing, event readout, and event creation.</p>
            </div>
          </div>

          <div className="grid gap-4 grid-cols-1 sm:grid-cols-2">
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Target Calendar</span>
              <select
                aria-label="Calendar selection"
                value={selectedCalendar}
                onChange={(e) => setSelectedCalendar(e.target.value)}
                className="glass-input w-full bg-black/30"
              >
                {calendars.map(cal => (
                  <option key={cal.id} value={cal.id}>{cal.display_name}</option>
                ))}
              </select>
            </label>
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Event Title</span>
              <input 
                type="text" 
                value={eventForm.summary}
                onChange={(e) => setEventForm({ ...eventForm, summary: e.target.value })}
                placeholder="Team Sync"
                className="glass-input w-full"
              />
            </label>
          </div>

          <div className="mt-4 grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-[1fr_auto]">
            <input
              type="text"
              value={eventForm.start}
              onChange={(e) => setEventForm({ ...eventForm, start: e.target.value })}
              className="glass-input"
              placeholder="Start time (e.g. tomorrow at 2pm)"
            />
            <button
              onClick={() => {
                if (!eventForm.summary.trim() || !eventForm.start.trim()) {
                  toast.error('Enter an event title and time');
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
            <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
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
