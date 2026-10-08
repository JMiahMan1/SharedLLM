import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronLeft, Loader2, Phone, PhoneOff, Plus, RefreshCw, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { ExecutionResponse } from '../../types/api';
import { useConversationSendAs } from './sendAsPref';
import ConversationList from './ConversationList';
import MessageThread from './MessageThread';
import Composer from './Composer';
import PollStrip, { type TalkPoll } from './PollStrip';
import {
  avatarTint,
  initials,
  reactionCounts,
  sortMessages,
  type TalkConversation,
  type TalkMessage,
} from './chatModel';

const EMPTY_ARRAY: never[] = [];

function detailList<T>(response: ExecutionResponse | undefined, key: string): T[] {
  const detail = response?.detail as Record<string, unknown> | undefined;
  const value = detail?.[key];
  return Array.isArray(value) ? (value as T[]) : [];
}

interface ChatPanelProps {
  className?: string;
}

/**
 * Family chat on Nextcloud Talk.
 *
 * Laid out like the messengers people already know (WhatsApp, Messenger,
 * iMessage): an inbox and a thread, which on a phone are two screens with a
 * back button and on a wide screen sit side by side. Text, voice notes,
 * replies, reactions, polls and calls all go through Talk, so these are the
 * same conversations the family uses in Nextcloud.
 */
export default function ChatPanel({ className = '' }: ChatPanelProps) {
  const queryClient = useQueryClient();
  const [selectedToken, setSelectedToken] = useState('');
  const [mobileView, setMobileView] = useState<'list' | 'thread'>('list');
  const [replyTo, setReplyTo] = useState<TalkMessage | null>(null);
  const [pending, setPending] = useState<Record<string, TalkMessage[]>>({});
  const [reactionOverrides, setReactionOverrides] = useState<Record<string, Record<string, number>>>({});
  const [showPollForm, setShowPollForm] = useState(false);
  const [pollQuestion, setPollQuestion] = useState('');
  const [pollOptions, setPollOptions] = useState(['', '']);
  const [inCall, setInCall] = useState(false);
  const [callError, setCallError] = useState<string | null>(null);

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000 });
  const isAdmin = Boolean(me?.is_admin);
  const myActorIds = useMemo(
    () =>
      new Set(
        [me?.nextcloud_user, me?.username].filter((v): v is string => Boolean(v)).map((v) => v.toLowerCase()),
      ),
    [me?.nextcloud_user, me?.username],
  );

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

  // Fall back to the first conversation so a wide screen is never blank.
  const activeToken = selectedToken || conversations[0]?.token || '';
  const activeConversation = conversations.find((c) => c.token === activeToken);
  const isGroup = activeConversation?.type !== 1;

  const [sendAs, setSendAs] = useConversationSendAs(isAdmin, activeToken);
  const asUser = sendAs === 'admin' ? ('admin' as const) : undefined;

  const { data: polls = EMPTY_ARRAY, refetch: refetchPolls } = useQuery<ExecutionResponse, Error, TalkPoll[]>({
    queryKey: ['talk-polls', activeToken],
    queryFn: () => api.getTalkPolls(activeToken),
    enabled: Boolean(activeToken),
    refetchInterval: 30000,
    select: (response) => detailList<TalkPoll>(response, 'polls'),
  });

  const { data: fetched = EMPTY_ARRAY, isFetching: loadingMessages } = useQuery<ExecutionResponse, Error, TalkMessage[]>({
    queryKey: ['talk-messages', activeToken],
    queryFn: () => api.getTalkMessages(activeToken),
    enabled: Boolean(activeToken),
    refetchInterval: 10000,
    select: (response) => sortMessages(detailList<TalkMessage>(response, 'messages')),
  });

  const messages = useMemo(() => [...fetched, ...(pending[activeToken] || [])], [fetched, pending, activeToken]);

  const markRead = useMutation({
    mutationFn: (token: string) => api.markTalkRead(token, asUser),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['talk-conversations'] }),
  });

  // Opening a conversation clears its badge -- that is the read marker.
  useEffect(() => {
    if (!activeToken || markRead.isPending) return;
    if ((activeConversation?.unread_messages ?? 0) > 0) markRead.mutate(activeToken);
  }, [activeToken, activeConversation?.unread_messages, markRead]);

  const select = (token: string) => {
    setSelectedToken(token);
    setMobileView('thread');
    setReplyTo(null);
    setShowPollForm(false);
    setInCall(false);
    setCallError(null);
  };

  const openConversation = useMutation({
    mutationFn: (user: string) => api.openTalkConversation({ target_user: user, as_user: asUser }),
    onSuccess: (data) => {
      const token = (data.detail as { conversation?: TalkConversation } | undefined)?.conversation?.token;
      if (token) select(token);
      queryClient.invalidateQueries({ queryKey: ['talk-conversations'] });
    },
    onError: (error: Error) => toast.error(error.message || 'Could not open that conversation'),
  });

  const updatePending = (token: string, fn: (list: TalkMessage[]) => TalkMessage[]) =>
    setPending((current) => ({ ...current, [token]: fn(current[token] || []) }));

  // Optimistic send: the bubble appears at once and is replaced by the real
  // message on the next fetch; a failure stays in the feed with Retry.
  const deliver = async (token: string, local: TalkMessage) => {
    updatePending(token, (list) => [...list.filter((m) => m.id !== local.id), { ...local, pending: 'sending' }]);
    try {
      const res = await api.sendTalkMessage({
        token,
        message: local.message || '',
        as_user: asUser,
        reply_to: local.reply_to,
      });
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Message failed to send');
      await queryClient.invalidateQueries({ queryKey: ['talk-messages', token] });
      updatePending(token, (list) => list.filter((m) => m.id !== local.id));
      queryClient.invalidateQueries({ queryKey: ['talk-conversations'] });
    } catch (error) {
      updatePending(token, (list) => list.map((m) => (m.id === local.id ? { ...m, pending: 'failed' } : m)));
      toast.error(error instanceof Error ? error.message : 'Message failed to send');
    }
  };

  const send = (text: string) => {
    if (!activeToken) {
      toast.error('Pick a conversation first');
      return;
    }
    const local: TalkMessage = {
      id: `local-${Date.now()}`,
      actor_display_name: 'You',
      message: text,
      timestamp: Math.floor(Date.now() / 1000),
      reply_to: typeof replyTo?.id === 'number' ? replyTo.id : undefined,
      parent:
        typeof replyTo?.id === 'number'
          ? { id: replyTo.id, actor_display_name: replyTo.actor_display_name, message: replyTo.message }
          : null,
    };
    setReplyTo(null);
    void deliver(activeToken, local);
  };

  const sendVoice = useMutation({
    mutationFn: (payload: { audio_base64: string; mime_type: string; caption?: string }) =>
      api.sendTalkVoice({ token: activeToken, ...payload, file_name: `voice-${Date.now()}.webm`, as_user: asUser }),
    onSuccess: () => {
      toast.success('Voice message sent');
      queryClient.invalidateQueries({ queryKey: ['talk-messages', activeToken] });
    },
    onError: (error: Error) => toast.error(error.message || 'Voice message failed'),
  });

  const call = useMutation({
    mutationFn: (leave: boolean) => (leave ? api.leaveTalkCall(activeToken, asUser) : api.joinTalkCall(activeToken, asUser)),
    onSuccess: (res, leave) => {
      if (res.status === 'SUCCESS') {
        setInCall(!leave);
        setCallError(null);
      } else {
        setCallError(res.message || (leave ? 'Could not leave the call' : 'Could not join the call'));
      }
    },
    onError: (error: Error) => setCallError(error.message || 'Could not start the call'),
  });

  const react = async (message: TalkMessage, emoji: string) => {
    if (typeof message.id !== 'number') return;
    const key = String(message.id);
    const before = reactionOverrides[key] ?? reactionCounts(message.reactions);
    setReactionOverrides((current) => ({ ...current, [key]: { ...before, [emoji]: (before[emoji] || 0) + 1 } }));
    try {
      const res = await api.reactToTalkMessage({ token: activeToken, message_id: message.id, reaction: emoji, as_user: asUser });
      const raw = (res.detail as { reactions?: unknown } | undefined)?.reactions;
      const counts = reactionCounts(raw);
      if (Object.keys(counts).length > 0) setReactionOverrides((current) => ({ ...current, [key]: counts }));
    } catch {
      setReactionOverrides((current) => ({ ...current, [key]: before }));
      toast.error('Could not react. Try again.');
    }
  };

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

  const name = activeConversation?.display_name || 'Select a conversation';

  return (
    <div
      className={`grid h-[calc(100dvh-13rem)] min-h-[440px] overflow-hidden rounded-3xl border border-white/5 bg-black/20 lg:h-[680px] lg:grid-cols-[320px_1fr] ${className}`}
      data-testid="chat-panel"
    >
      {/* Inbox */}
      <aside className={`min-h-0 flex-col border-white/5 p-3 lg:flex lg:border-r ${mobileView === 'list' ? 'flex' : 'hidden'}`}>
        <ConversationList
          conversations={conversations}
          activeToken={activeToken}
          myActorIds={myActorIds}
          loading={loadingConversations}
          opening={openConversation.isPending}
          onSelect={select}
          onStart={(user) => openConversation.mutate(user)}
        />
      </aside>

      {/* Thread */}
      <section className={`min-h-0 min-w-0 flex-col lg:flex ${mobileView === 'thread' ? 'flex' : 'hidden'}`}>
        <header className="flex shrink-0 items-center gap-2 border-b border-white/5 px-2 py-2 sm:px-3">
          <button
            type="button"
            className="flex min-h-10 min-w-10 items-center justify-center rounded-full text-slate-300 hover:bg-white/10 lg:hidden"
            aria-label="Back to chats"
            onClick={() => setMobileView('list')}
          >
            <ChevronLeft size={22} />
          </button>
          {activeConversation && (
            <span
              aria-hidden
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
              style={{ background: avatarTint(name) }}
            >
              {initials(name)}
            </span>
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate font-semibold text-slate-100">{name}</p>
            <p className="truncate text-[11px] text-slate-500">
              {callError ?? (inCall ? 'In call' : activeConversation?.description || (isGroup ? 'Group chat' : 'Direct message'))}
            </p>
          </div>
          {activeToken && (
            <button
              type="button"
              className={`flex min-h-10 min-w-10 items-center justify-center rounded-full ${
                inCall ? 'bg-rose-500 text-white' : 'text-slate-300 hover:bg-white/10'
              }`}
              onClick={() => call.mutate(inCall)}
              disabled={call.isPending}
              aria-pressed={inCall}
              aria-label={inCall ? 'Leave the call' : 'Join the call'}
              title={callError ?? (inCall ? 'Leave the call' : 'Join the call')}
            >
              {call.isPending ? <Loader2 size={17} className="animate-spin" /> : inCall ? <PhoneOff size={17} /> : <Phone size={17} />}
            </button>
          )}
          <button
            type="button"
            className="flex min-h-10 min-w-10 items-center justify-center rounded-full text-slate-400 hover:bg-white/10"
            aria-label="Refresh messages"
            onClick={() => queryClient.invalidateQueries({ queryKey: ['talk-messages', activeToken] })}
          >
            <RefreshCw size={15} className={loadingMessages ? 'animate-spin' : ''} />
          </button>
        </header>

        <PollStrip polls={polls} onVote={(pollId, optionId) => void vote(pollId, optionId)} />

        <MessageThread
          key={activeToken}
          messages={messages}
          myActorIds={myActorIds}
          isGroup={isGroup}
          reactionCounts={(message) =>
            (typeof message.id === 'number' && reactionOverrides[String(message.id)]) || reactionCounts(message.reactions)
          }
          onReact={(message, emoji) => void react(message, emoji)}
          onReply={setReplyTo}
          onRetry={(message) => void deliver(activeToken, message)}
          onDiscard={(message) => updatePending(activeToken, (list) => list.filter((m) => m.id !== message.id))}
          emptyText={activeToken ? 'No messages yet. Say hello!' : 'Pick a conversation to see messages.'}
        />

        {showPollForm && (
          <div className="shrink-0 space-y-2 border-t border-white/5 bg-white/[0.03] p-3" data-testid="poll-form">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-slate-100">New poll</p>
              <button type="button" aria-label="Close poll form" className="p-1 text-slate-400" onClick={() => setShowPollForm(false)}>
                <X size={15} />
              </button>
            </div>
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
                  onChange={(event) => setPollOptions((prev) => prev.map((o, i) => (i === index ? event.target.value : o)))}
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

        <Composer
          disabled={!activeToken}
          replyTo={replyTo}
          onCancelReply={() => setReplyTo(null)}
          onSend={send}
          onSendVoice={async (payload) => {
            try {
              await sendVoice.mutateAsync(payload);
              return true;
            } catch {
              return false;
            }
          }}
          sendingVoice={sendVoice.isPending}
          onNewPoll={() => setShowPollForm(true)}
          isAdmin={isAdmin}
          sendAs={sendAs}
          onSendAsChange={setSendAs}
        />
      </section>
    </div>
  );
}
