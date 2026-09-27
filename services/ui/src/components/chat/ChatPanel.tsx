import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BarChart3, Loader2, MessageSquare, Mic, Plus, RefreshCw, Send, Square } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { ExecutionResponse } from '../../types/api';
import SendAsSelector, { useSendAsPref } from './SendAsSelector';

interface TalkConversation {
  token: string;
  display_name: string;
  description?: string;
  last_message?: string;
}

interface TalkPollOption {
  id: number;
  label: string;
  numVotes?: number;
}

interface TalkPoll {
  id: number;
  question: string;
  options?: TalkPollOption[];
  status?: number;
}

interface TalkReaction {
  reaction?: string;
  actor_display_name?: string;
}

interface TalkMessage {
  id?: number | string;
  actor_display_name?: string;
  message?: string;
  system_message?: string;
  timestamp?: number;
}

const EMPTY_ARRAY: never[] = [];

function detailList<T>(response: ExecutionResponse | undefined, key: string): T[] {
  const detail = response?.detail as Record<string, unknown> | undefined;
  const value = detail?.[key];
  return Array.isArray(value) ? (value as T[]) : [];
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Stable per-person tint so a family member is recognisable at a glance. */
function avatarTint(name: string): string {
  const palette = ['#863BFF', '#0EA5E9', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6', '#14B8A6', '#F43F5E'];
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return palette[hash % palette.length];
}

interface ChatPanelProps {
  /** Current user's display name, used to align "my" messages to the right. */
  currentUser?: string;
  className?: string;
}

/**
 * Family chat on Nextcloud Talk.
 *
 * Text and voice messages go through Talk, so conversations are the same ones
 * the family already uses in Nextcloud (and on the Skylight board). Voice and
 * video calls need Talk WebRTC signalling and are the next slice; this panel
 * is deliberately room-based so a call button can be added without a rewrite.
 */
export default function ChatPanel({ currentUser = '', className = '' }: ChatPanelProps) {
  const queryClient = useQueryClient();
  const [selectedToken, setSelectedToken] = useState('');
  const [targetUser, setTargetUser] = useState('');
  const [draft, setDraft] = useState('');
  const [recording, setRecording] = useState(false);
  const [clip, setClip] = useState<{ url: string; base64: string; mimeType: string } | null>(null);
  const [caption, setCaption] = useState('');
  const [reactingFor, setReactingFor] = useState<number | null>(null);
  const [reactions, setReactions] = useState<Record<number, TalkReaction[]>>({});
  const [showPollForm, setShowPollForm] = useState(false);
  const [pollQuestion, setPollQuestion] = useState('');
  const [pollOptions, setPollOptions] = useState(['', '']);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const feedRef = useRef<HTMLDivElement | null>(null);

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000 });
  const isAdmin = Boolean(me?.is_admin);
  const [sendAs, setSendAs] = useSendAsPref(isAdmin);
  const asUser = isAdmin && sendAs === 'admin' ? ('admin' as const) : undefined;

  const { data: conversations = EMPTY_ARRAY, isFetching: loadingConversations } = useQuery<
    ExecutionResponse,
    Error,
    TalkConversation[]
  >({
    queryKey: ['talk-conversations'],
    queryFn: () => api.getTalkConversations(),
    refetchInterval: 15000,
    select: (response) => detailList<TalkConversation>(response, 'conversations'),
  });

  // Fall back to the first conversation so the panel is usable on open
  // without an effect that writes state during render.
  const activeToken = selectedToken || conversations[0]?.token || '';

  const { data: polls = EMPTY_ARRAY, refetch: refetchPolls } = useQuery<
    ExecutionResponse,
    Error,
    TalkPoll[]
  >({
    queryKey: ['talk-polls', activeToken],
    queryFn: () => api.getTalkPolls(activeToken),
    enabled: Boolean(activeToken),
    refetchInterval: 30000,
    select: (response) => detailList<TalkPoll>(response, 'polls'),
  });

  const { data: messages = EMPTY_ARRAY, isFetching: loadingMessages } = useQuery<
    ExecutionResponse,
    Error,
    TalkMessage[]
  >({
    queryKey: ['talk-messages', activeToken],
    queryFn: () => api.getTalkMessages(activeToken),
    enabled: Boolean(activeToken),
    refetchInterval: 10000,
    select: (response) => detailList<TalkMessage>(response, 'messages'),
  });

  useEffect(() => {
    // Keep the newest message in view as the feed grows.
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [messages]);

  const openConversation = useMutation({
    mutationFn: (user: string) => api.openTalkConversation({ target_user: user, as_user: asUser }),
    onSuccess: (data) => {
      const token = (data.detail as { conversation?: TalkConversation } | undefined)?.conversation?.token;
      if (token) setSelectedToken(token);
      setTargetUser('');
      queryClient.invalidateQueries({ queryKey: ['talk-conversations'] });
    },
    onError: (error: Error) => toast.error(error.message || 'Could not open that conversation'),
  });

  const sendMessage = useMutation({
    mutationFn: (text: string) => api.sendTalkMessage({ token: activeToken, message: text, as_user: asUser }),
    onSuccess: () => {
      setDraft('');
      queryClient.invalidateQueries({ queryKey: ['talk-messages', activeToken] });
    },
    onError: (error: Error) => toast.error(error.message || 'Message failed to send'),
  });

  const sendVoice = useMutation({
    mutationFn: (payload: { audio_base64: string; mime_type: string; caption?: string }) =>
      api.sendTalkVoice({ token: activeToken, ...payload, file_name: `voice-${Date.now()}.webm`, as_user: asUser }),
    onSuccess: () => {
      toast.success('Voice message sent');
      if (clip) URL.revokeObjectURL(clip.url);
      setClip(null);
      setCaption('');
      queryClient.invalidateQueries({ queryKey: ['talk-messages', activeToken] });
    },
    onError: (error: Error) => toast.error(error.message || 'Voice message failed'),
  });

  const activeConversation = useMemo(
    () => conversations.find((c) => c.token === activeToken),
    [conversations, selectedToken]
  );

  const submit = () => {
    const text = draft.trim();
    if (!activeToken) {
      toast.error('Pick a conversation first');
      return;
    }
    if (!text) return;
    sendMessage.mutate(text);
  };

  const startRecording = async () => {
    if (!activeToken) {
      toast.error('Pick a conversation first');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      toast.error('Recording is not available on this device');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        if (blob.size === 0) return;
        const buffer = await blob.arrayBuffer();
        let binary = '';
        const bytes = new Uint8Array(buffer);
        for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
        // Preview before sending: recording is easy to do by accident, and a
        // caption often carries the point of a voice note.
        setClip({ url: URL.createObjectURL(blob), base64: btoa(binary), mimeType: blob.type });
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      toast.error('Microphone permission is needed to record a voice message');
    }
  };

  const stopRecording = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  const QUICK_REACTIONS = ['👍', '❤️', '😂', '🎉', '🙏', '😮'];

  const submitPoll = async () => {
    const question = pollQuestion.trim();
    const options = pollOptions.map((o) => o.trim()).filter(Boolean);
    if (!question || options.length < 2) {
      toast.error('A poll needs a question and at least two options');
      return;
    }
    try {
      await api.createTalkPoll({ token: activeToken, question, options, as_user: asUser });
      setShowPollForm(false);
      setPollQuestion('');
      setPollOptions(['', '']);
      toast.success('Poll posted');
      await refetchPolls();
    } catch {
      toast.error('Could not create the poll');
    }
  };

  const vote = async (pollId: number, optionId: number) => {
    try {
      await api.voteTalkPoll({ token: activeToken, poll_id: pollId, option_id: optionId, as_user: asUser });
      await refetchPolls();
    } catch {
      toast.error('Could not record your vote');
    }
  };

  const loadReactions = async (messageId: number) => {
    try {
      const res = await api.getTalkReactions(activeToken, messageId);
      const list = (res.detail as { reactions?: TalkReaction[] } | undefined)?.reactions ?? [];
      setReactions((prev) => ({ ...prev, [messageId]: list }));
    } catch {
      // Reactions are additive; failing to load them must not break chat.
    }
  };

  const react = async (messageId: number, reaction: string) => {
    setReactingFor(null);
    try {
      const res = await api.reactToTalkMessage({ token: activeToken, message_id: messageId, reaction, as_user: asUser });
      const list = (res.detail as { reactions?: TalkReaction[] } | undefined)?.reactions;
      if (Array.isArray(list)) setReactions((prev) => ({ ...prev, [messageId]: list }));
      else void loadReactions(messageId);
    } catch {
      toast.error('Could not react — try again');
    }
  };

  const openReactionBar = (messageId: number) => {
    setReactingFor((current) => (current === messageId ? null : messageId));
    if (!reactions[messageId]) void loadReactions(messageId);
  };

  return (
    <div className={`grid gap-4 lg:grid-cols-[280px_1fr] ${className}`} data-testid="chat-panel">
      {/* Conversations */}
      <div className="space-y-2 min-w-0">
        <div className="flex items-center gap-2">
          <input
            value={targetUser}
            onChange={(event) => setTargetUser(event.target.value)}
            placeholder="Start a chat (username)"
            aria-label="Start a conversation with"
            className="glass-input flex-1 text-sm"
            onKeyDown={(event) => {
              if (event.key === 'Enter' && targetUser.trim()) openConversation.mutate(targetUser.trim());
            }}
          />
          <button
            type="button"
            aria-label="Open conversation"
            className="glass-button p-2.5"
            onClick={() => {
              if (!targetUser.trim()) {
                toast.error('Enter a username first');
                return;
              }
              openConversation.mutate(targetUser.trim());
            }}
          >
            <Plus size={16} />
          </button>
        </div>

        <div className="flex max-h-24 gap-2 overflow-x-auto overscroll-contain pb-1 lg:max-h-none lg:block lg:space-y-2 lg:overflow-visible lg:pb-0 lg:max-h-[60vh] lg:overflow-y-auto lg:pr-1">
          {conversations.map((conversation) => (
            <button
              key={conversation.token}
              type="button"
              onClick={() => setSelectedToken(conversation.token)}
              className={`flex shrink-0 items-center gap-2 rounded-2xl border p-2.5 text-left transition min-h-11 lg:w-full lg:gap-3 lg:p-3 lg:shrink ${
                activeToken === conversation.token
                  ? 'border-purple-400/40 bg-purple-500/10'
                  : 'border-white/5 bg-white/5 hover:border-white/10 hover:bg-white/10'
              }`}
            >
              <span
                aria-hidden
                className="h-9 w-9 shrink-0 rounded-full flex items-center justify-center text-xs font-bold text-white"
                style={{ background: avatarTint(conversation.display_name || conversation.token) }}
              >
                {initials(conversation.display_name || conversation.token)}
              </span>
              <span className="hidden min-w-0 lg:block">
                <span className="block font-semibold text-slate-100 truncate">{conversation.display_name}</span>
                <span className="block text-xs text-slate-400 truncate">
                  {conversation.last_message || 'No messages yet'}
                </span>
              </span>
              <span className="max-w-24 truncate text-xs font-semibold text-slate-200 lg:hidden">
                {(conversation.display_name || conversation.token).split(/\s+/)[0]}
              </span>
            </button>
          ))}
          {conversations.length === 0 && (
            <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
              {loadingConversations ? 'Loading conversations…' : 'No conversations yet — start one above.'}
            </p>
          )}
        </div>
      </div>

      {/* Feed + composer */}
      <div className="flex h-[calc(100dvh-17rem)] min-h-[360px] flex-col overflow-hidden rounded-2xl border border-white/5 bg-black/20 lg:h-[560px]">
        <div className="flex items-center justify-between gap-2 border-b border-white/5 px-4 py-3">
          <div className="flex items-center gap-2 min-w-0">
            <MessageSquare size={16} className="text-fuchsia-300 shrink-0" />
            <span className="font-semibold text-slate-100 truncate">
              {activeConversation?.display_name || 'Select a conversation'}
            </span>
          </div>
          <button
            type="button"
            className="glass-button p-2"
            aria-label={showPollForm ? 'Close poll form' : 'New poll'}
            onClick={() => setShowPollForm((v) => !v)}
          >
            <BarChart3 size={14} />
          </button>
          <button
            type="button"
            className="glass-button p-2"
            aria-label="Refresh messages"
            onClick={() => queryClient.invalidateQueries({ queryKey: ['talk-messages', selectedToken] })}
          >
            <RefreshCw size={14} className={loadingMessages ? 'animate-spin' : ''} />
          </button>
        </div>

        {showPollForm && (
          <div className="shrink-0 space-y-2 border-b border-white/5 bg-white/[0.03] p-3" data-testid="poll-form">
            <input
              value={pollQuestion}
              onChange={(event) => setPollQuestion(event.target.value)}
              placeholder="What should we do for dinner?"
              aria-label="Poll question"
              className="glass-input w-full text-base sm:text-sm"
            />
            {pollOptions.map((option, index) => (
              <div key={index} className="flex gap-2">
                <input
                  value={option}
                  onChange={(event) =>
                    setPollOptions((prev) => prev.map((o, i) => (i === index ? event.target.value : o)))
                  }
                  placeholder={`Option ${index + 1}`}
                  aria-label={`Poll option ${index + 1}`}
                  className="glass-input flex-1 text-base sm:text-sm"
                />
                {pollOptions.length > 2 && (
                  <button
                    type="button"
                    className="glass-button px-3"
                    aria-label={`Remove option ${index + 1}`}
                    onClick={() => setPollOptions((prev) => prev.filter((_, i) => i !== index))}
                  >
                    −
                  </button>
                )}
              </div>
            ))}
            <div className="flex gap-2">
              {pollOptions.length < 4 && (
                <button type="button" className="glass-button px-3 py-2 text-sm" onClick={() => setPollOptions((prev) => [...prev, ''])}>
                  <Plus size={14} /> Option
                </button>
              )}
              <button type="button" className="glass-button flex-1 py-2 text-sm" onClick={() => void submitPoll()}>
                Post poll
              </button>
            </div>
          </div>
        )}

        {polls.length > 0 && (
          <div className="shrink-0 space-y-2 border-b border-white/5 p-3" data-testid="poll-list">
            {polls.slice(0, 3).map((poll) => (
              <div key={poll.id} className="rounded-xl border border-white/10 bg-white/5 p-2">
                <p className="text-sm font-semibold text-slate-100">{poll.question}</p>
                <div className="mt-1 space-y-1">
                  {(poll.options || []).map((option) => (
                    <button
                      key={option.id}
                      type="button"
                      onClick={() => void vote(poll.id, option.id)}
                      className="flex w-full items-center justify-between rounded-lg border border-white/10 px-2 py-1.5 text-left text-xs text-slate-200 min-h-9"
                    >
                      <span>{option.label}</span>
                      <span className="text-slate-400">{option.numVotes ?? 0}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        <div ref={feedRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain px-4 py-4" data-testid="chat-feed">
          {messages.map((message, index) => {
            const author = message.actor_display_name || 'Someone';
            const mine = currentUser && author.toLowerCase() === currentUser.toLowerCase();
            return (
              <div key={message.id ?? `${message.timestamp}-${index}`} className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[85%] sm:max-w-[80%] ${mine ? 'text-right' : ''}`}>
                  <div className={`flex items-center gap-2 ${mine ? 'justify-end' : ''}`}>
                    <span className="text-[11px] font-semibold text-slate-300">{mine ? 'You' : author}</span>
                    {message.timestamp && (
                      <span className="text-[10px] text-slate-500">
                        {new Date(message.timestamp * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => typeof message.id === 'number' && openReactionBar(message.id)}
                    className={`mt-1 inline-block rounded-2xl px-3 py-2 text-sm break-words text-left ${
                      mine ? 'bg-purple-500/25 text-slate-100' : 'bg-white/10 text-slate-100'
                    }`}
                    title="Tap to react"
                  >
                    {message.message || message.system_message || 'Empty message'}
                  </button>

                  {typeof message.id === 'number' && (reactions[message.id]?.length ?? 0) > 0 && (
                    <div className={`mt-1 flex flex-wrap gap-1 ${mine ? 'justify-end' : ''}`} data-testid="reaction-chips">
                      {Object.entries(
                        (reactions[message.id] || []).reduce<Record<string, number>>((acc, item) => {
                          const key = item.reaction || '👍';
                          acc[key] = (acc[key] || 0) + 1;
                          return acc;
                        }, {})
                      ).map(([emoji, count]) => (
                        <span key={emoji} className="rounded-full border border-white/10 bg-white/5 px-1.5 py-0.5 text-[11px]">
                          {emoji} {count > 1 ? count : ''}
                        </span>
                      ))}
                    </div>
                  )}

                  {reactingFor === message.id && (
                    <div className={`mt-1 flex gap-1 ${mine ? 'justify-end' : ''}`} data-testid="reaction-bar">
                      {QUICK_REACTIONS.map((emoji) => (
                        <button
                          key={emoji}
                          type="button"
                          aria-label={`React ${emoji}`}
                          className="min-h-9 min-w-9 rounded-full border border-white/10 bg-white/5 text-base"
                          onClick={() => typeof message.id === 'number' && void react(message.id, emoji)}
                        >
                          {emoji}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          {messages.length === 0 && (
            <p className="text-sm text-slate-500">
              {selectedToken ? 'No messages yet — say hello!' : 'Pick a conversation to see messages.'}
            </p>
          )}
        </div>

        <div className="shrink-0 space-y-2 border-t border-white/5 bg-slate-950/80 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur">
          {isAdmin && <SendAsSelector value={sendAs} onChange={setSendAs} />}
          <div className="flex items-end gap-2">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  submit();
                }
              }}
              rows={2}
              placeholder="Write a message…"
              aria-label="Message"
              className="glass-input flex-1 resize-none text-base sm:text-sm"
            />
            <button
              type="button"
              className="glass-button min-h-11 min-w-11 px-4 py-3"
              onClick={submit}
              disabled={sendMessage.isPending || !draft.trim()}
              aria-label="Send message"
            >
              {sendMessage.isPending ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
            </button>
            <button
              type="button"
              className={`glass-button min-h-11 min-w-11 px-4 py-3 ${recording ? 'text-rose-300' : ''}`}
              onClick={() => (recording ? stopRecording() : void startRecording())}
              disabled={sendVoice.isPending}
              aria-label={recording ? 'Stop recording' : 'Record a voice message'}
              aria-pressed={recording}
            >
              {recording ? <Square size={16} /> : <Mic size={16} />}
            </button>
          </div>
          {clip && (
            <div className="rounded-xl border border-white/10 bg-white/5 p-2 space-y-2" data-testid="voice-preview">
              <p className="text-[11px] text-slate-300">Recorded clip ready</p>
              <audio controls src={clip.url} className="w-full h-9" />
              <input
                value={caption}
                onChange={(event) => setCaption(event.target.value)}
                placeholder="Optional caption for voice message"
                aria-label="Voice message caption"
                className="glass-input w-full text-sm"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  className="glass-button flex-1 min-h-11 px-3 py-2 text-sm"
                  disabled={sendVoice.isPending}
                  onClick={() =>
                    sendVoice.mutate({ audio_base64: clip.base64, mime_type: clip.mimeType, caption: caption.trim() })
                  }
                >
                  {sendVoice.isPending ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />} Send voice
                </button>
                <button
                  type="button"
                  className="glass-button px-3 py-2 text-sm"
                  onClick={() => {
                    URL.revokeObjectURL(clip.url);
                    setClip(null);
                    setCaption('');
                  }}
                >
                  Discard
                </button>
              </div>
            </div>
          )}
          <p className="text-[10px] text-slate-500">
            Enter sends · Shift+Enter adds a line · voice messages post straight into Talk.
          </p>
        </div>
      </div>
    </div>
  );
}
