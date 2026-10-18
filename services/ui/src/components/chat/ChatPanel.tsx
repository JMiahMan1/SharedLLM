import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import ChatNotify from '../../plugins/chatNotify';
import { useKeyboardSafeHeight } from '../../lib/useKeyboardSafeHeight';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Brush, ChevronLeft, Gamepad2, ImageIcon, Info, Phone, Plus, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { ExecutionResponse } from '../../types/api';
import { useConversationSendAs } from './sendAsPref';
import ConversationList from './ConversationList';
import ConversationInfo from './ConversationInfo';
import NewChatSheet from './NewChatSheet';
import ConversationAvatar from './ConversationAvatar';
import VerseBanner from './VerseBanner';
import { verseShareText } from '../../lib/verseShare';
import { useFamilyDirectory } from './familyDirectory';
import { useCall } from '../../context/CallContext';
import MessageThread from './MessageThread';
import Composer from './Composer';
import PollStrip, { type TalkPoll } from './PollStrip';
import {
  asksJarvis,
  detailList,
  isAssistant,
  MAX_ATTACHMENT_BYTES,
  reactionCounts,
  sortMessages,
  type TalkConversation,
  type TalkMessage,
} from './chatModel';

const EMPTY_ARRAY: never[] = [];

/** How long to show "Jarvis is thinking" before giving up on an answer. */
const JARVIS_WAIT_MS = 3 * 60 * 1000;
/** Messages loaded per conversation, and each "Show earlier" step (Talk's cap is 200). */
const PAGE = 50;
const MAX_HISTORY = 200;

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

interface ChatPanelProps {
  className?: string;
  /** Open another part of the Family hub (the Games or Create tab). */
  onOpenTab?: (tab: 'games' | 'create') => void;
}

/**
 * Family chat on Nextcloud Talk, with Jarvis in the room.
 *
 * Laid out like the messengers people already know (WhatsApp, Messenger,
 * iMessage): an inbox and a thread, which on a phone are two screens with a
 * back button and on a wide screen sit side by side. Text, photos, files,
 * voice notes, replies, reactions, edits and polls go through Talk, so these
 * are the same conversations the family uses in Nextcloud. Calls are different:
 * the phone button starts a peer-to-peer call inside Jarvis, signalled by the
 * gateway. Starting a message with @Jarvis asks the assistant, which answers in
 * the thread.
 */
export default function ChatPanel({ className = '', onOpenTab }: ChatPanelProps) {
  const queryClient = useQueryClient();
  const [selectedToken, setSelectedToken] = useState('');
  // 'auto' until the reader picks: a phone opens straight into the default
  // conversation when one is set, otherwise on the list.
  const [mobileView, setMobileView] = useState<'auto' | 'list' | 'thread'>('auto');
  const [infoOpen, setInfoOpen] = useState(false);
  const [newChatOpen, setNewChatOpen] = useState(false);
  const [replyTo, setReplyTo] = useState<TalkMessage | null>(null);
  const [editing, setEditing] = useState<TalkMessage | null>(null);
  const [pending, setPending] = useState<Record<string, TalkMessage[]>>({});
  const [reactionOverrides, setReactionOverrides] = useState<Record<string, Record<string, number>>>({});
  const [staged, setStaged] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [jarvisAsked, setJarvisAsked] = useState<Record<string, number>>({});
  const [now, setNow] = useState(() => Date.now());
  const [unreadMarkers, setUnreadMarkers] = useState<Record<string, number | undefined>>({});
  const [showPollForm, setShowPollForm] = useState(false);
  const [pollQuestion, setPollQuestion] = useState('');
  const [pollOptions, setPollOptions] = useState(['', '']);
  const [historyLimit, setHistoryLimit] = useState<Record<string, number>>({});
  const { startCall } = useCall();

  const threadRef = useRef<HTMLElement | null>(null);
  // Keep the composer above the phone keyboard (iOS does not resize the page for it).
  const keyboardHeight = useKeyboardSafeHeight(threadRef);

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000 });
  const isAdmin = Boolean(me?.is_admin);
  const people = useFamilyDirectory();
  const myActorIds = useMemo(
    () => new Set([me?.nextcloud_user, me?.username].filter((v): v is string => Boolean(v)).map((v) => v.toLowerCase())),
    [me?.nextcloud_user, me?.username],
  );

  const {
    data: conversations = EMPTY_ARRAY,
    isFetching: loadingConversations,
    isError: conversationsError,
    error: conversationsErrorObj,
    refetch: refetchConversations,
  } = useQuery<ExecutionResponse, Error, TalkConversation[]>({
    queryKey: ['talk-conversations'],
    queryFn: async () => {
      const response = await api.getTalkConversations();
      if (response.status === 'FAILURE') {
        throw new Error(response.message || 'Could not load conversations');
      }
      return response;
    },
    refetchInterval: 15000,
    retry: false,
    placeholderData: keepPreviousData,
    select: (response) => detailList<TalkConversation>(response, 'conversations'),
  });

  const conversationsErrorMessage = conversationsErrorObj?.message?.toLowerCase().includes('timeout')
    ? 'The family server is taking too long to answer.'
    : conversationsErrorObj?.message || 'Could not load conversations.';

  // The conversation set to open on launch, if it still exists.
  const defaultToken = conversations.some((c) => c.token === me?.chat_default_token) ? (me?.chat_default_token ?? '') : '';
  // Fall back to the first conversation so a wide screen is never blank.
  const activeToken = selectedToken || defaultToken || conversations[0]?.token || '';
  const showThread = mobileView === 'thread' || (mobileView === 'auto' && Boolean(defaultToken));
  const activeConversation = conversations.find((c) => c.token === activeToken);
  const isGroup = activeConversation?.type !== 1;

  const [sendAs, setSendAs] = useConversationSendAs(isAdmin, activeToken);
  const asUser = sendAs === 'admin' ? ('admin' as const) : undefined;

  const askedAt = jarvisAsked[activeToken];
  const waitingForJarvis = askedAt !== undefined && now - askedAt < JARVIS_WAIT_MS;

  const { data: polls = EMPTY_ARRAY, refetch: refetchPolls } = useQuery<ExecutionResponse, Error, TalkPoll[]>({
    queryKey: ['talk-polls', activeToken],
    queryFn: () => api.getTalkPolls(activeToken),
    enabled: Boolean(activeToken),
    refetchInterval: 30000,
    select: (response) => detailList<TalkPoll>(response, 'polls'),
  });

  const limit = historyLimit[activeToken] ?? PAGE;
  const { data: fetched = EMPTY_ARRAY, isFetching: fetchingMessages } = useQuery<ExecutionResponse, Error, TalkMessage[]>({
    queryKey: ['talk-messages', activeToken, limit],
    queryFn: () => api.getTalkMessages(activeToken, limit),
    enabled: Boolean(activeToken),
    // While "Show earlier" loads, keep this conversation's messages on screen
    // (never another conversation's).
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[1] === activeToken ? previous : undefined),
    // Poll briskly while Jarvis is working so the answer lands promptly.
    refetchInterval: waitingForJarvis ? 2500 : 10000,
    select: (response) => sortMessages(detailList<TalkMessage>(response, 'messages')),
  });

  const messages = useMemo(() => [...fetched, ...(pending[activeToken] || [])], [fetched, pending, activeToken]);
  // "Show earlier" asked for more than is on screen and the fetch is out.
  const loadingEarlier = fetchingMessages && fetched.length > 0 && fetched.length < limit;

  // Jarvis has answered once an assistant message newer than the question shows up.
  const jarvisAnswered =
    askedAt !== undefined && fetched.some((m) => isAssistant(m) && (m.timestamp ?? 0) * 1000 >= askedAt - 5000);
  const jarvisThinking = waitingForJarvis && !jarvisAnswered;

  // A clock for the thinking indicator's timeout, ticking only while it shows.
  useEffect(() => {
    if (!jarvisThinking) return;
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, [jarvisThinking]);

  const markRead = useMutation({
    mutationFn: (token: string) => api.markTalkRead(token, asUser),
    // Remember where you were before this visit, for the "New messages" line.
    onMutate: (token: string) => {
      const conversation = conversations.find((c) => c.token === token);
      setUnreadMarkers((current) => (token in current ? current : { ...current, [token]: conversation?.last_read }));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['talk-conversations'] }),
  });

  // Opening a conversation clears its badge -- that is the read marker. Once
  // per conversation per new activity: the mutation object changes every
  // render, and the unread count lags the request, so keying on either alone
  // re-sent the read in a loop.
  const markedRef = useRef<string>('');
  const unread = activeConversation?.unread_messages ?? 0;
  const readKey = `${activeToken}:${activeConversation?.last_message_timestamp ?? activeConversation?.last_activity ?? ''}`;
  const { mutate: markReadNow } = markRead;
  useEffect(() => {
    if (!activeToken || unread <= 0 || markedRef.current === readKey) return;
    markedRef.current = readKey;
    markReadNow(activeToken);
  }, [activeToken, unread, readKey, markReadNow]);

  const setDefault = useMutation({
    mutationFn: (token: string | null) =>
      api.updateProfile(token ? { chat_default_token: token } : ({ clear_fields: ['chat_default_token'] } as never)),
    onSuccess: (_r, token) => {
      void queryClient.invalidateQueries({ queryKey: ['me'] });
      toast.success(token ? 'Chat will open here' : 'No conversation opens on launch');
    },
    onError: (error: Error) => toast.error(error.message || 'Could not save that'),
  });

  const createGroup = useMutation({
    mutationFn: ({ name, people }: { name: string; people: string[] }) => api.createTalkGroup(name, people),
    onSuccess: (data) => {
      const token = (data.detail as { conversation?: TalkConversation } | undefined)?.conversation?.token;
      if (data.message) toast.success(data.message);
      setNewChatOpen(false);
      void queryClient.invalidateQueries({ queryKey: ['talk-conversations'] });
      if (token) select(token);
    },
    onError: (error: Error) => toast.error(error.message || 'Could not create the group'),
  });

  const select = (token: string) => {
    setSelectedToken(token);
    setMobileView('thread');
    setReplyTo(null);
    setEditing(null);
    setStaged([]);
    setShowPollForm(false);
  };

  // A tapped phone notification lands on /family?chat=<token>: open it, then
  // drop the parameter so going back to the list is not undone.
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedToken = searchParams.get('chat');
  useEffect(() => {
    if (!linkedToken) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- following the link
    setSelectedToken(linkedToken);
    setMobileView('thread');
    setSearchParams((params) => {
      params.delete('chat');
      return params;
    }, { replace: true });
  }, [linkedToken, setSearchParams]);

  // Tell the phone which conversation is on screen, so it does not notify
  // about messages being read as they arrive.
  const onScreenToken = showThread || window.innerWidth >= 1024 ? activeToken : '';
  useEffect(() => {
    if (!Capacitor.isNativePlatform()) return;
    void ChatNotify.setActiveConversation({ token: onScreenToken || null }).catch(() => {});
    return () => {
      void ChatNotify.setActiveConversation({ token: null }).catch(() => {});
    };
  }, [onScreenToken]);

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

  const refreshThread = (token: string) => {
    queryClient.invalidateQueries({ queryKey: ['talk-messages', token] });
    queryClient.invalidateQueries({ queryKey: ['talk-conversations'] });
  };

  // Optimistic send: the bubble appears at once and is replaced by the real
  // message on the next fetch; a failure stays in the feed with Retry.
  const deliver = async (token: string, local: TalkMessage) => {
    updatePending(token, (list) => [...list.filter((m) => m.id !== local.id), { ...local, pending: 'sending' }]);
    try {
      const res = await api.sendTalkMessage({ token, message: local.message || '', as_user: asUser, reply_to: local.reply_to });
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Message failed to send');
      if (asksJarvis(local.message || '')) {
        setNow(Date.now());
        setJarvisAsked((current) => ({ ...current, [token]: Date.now() }));
      }
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
    const parentId = typeof replyTo?.id === 'number' ? replyTo.id : undefined;
    const local: TalkMessage = {
      id: `local-${Date.now()}`,
      actor_display_name: 'You',
      message: text,
      timestamp: Math.floor(Date.now() / 1000),
      reply_to: parentId,
      parent: parentId ? { id: parentId, actor_display_name: replyTo?.actor_display_name, message: replyTo?.message } : null,
    };
    setReplyTo(null);
    void deliver(activeToken, local);
  };

  const saveEdit = async (text: string) => {
    const target = editing;
    setEditing(null);
    if (!target || typeof target.id !== 'number') return;
    try {
      const res = await api.editTalkMessage({ token: activeToken, message_id: target.id, message: text, as_user: asUser });
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Could not edit the message');
      refreshThread(activeToken);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not edit the message');
    }
  };

  const remove = async (message: TalkMessage) => {
    if (typeof message.id !== 'number') return;
    try {
      const res = await api.deleteTalkMessage({ token: activeToken, message_id: message.id, as_user: asUser });
      if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || 'Could not delete the message');
      refreshThread(activeToken);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not delete the message');
    }
  };

  const sendFiles = useMutation({
    mutationFn: async (caption: string) => {
      const files = [...staged];
      for (const [index, file] of files.entries()) {
        const res = await api.sendTalkFile({
          token: activeToken,
          file_base64: await fileToBase64(file),
          mime_type: file.type || 'application/octet-stream',
          file_name: file.name,
          // The caption rides on the first file, like a photo album's.
          caption: index === 0 && caption ? caption : undefined,
          as_user: asUser,
        });
        if (res.status && res.status !== 'SUCCESS') throw new Error(res.message || `Could not send ${file.name}`);
      }
      return files.length;
    },
    onSuccess: (count) => {
      setStaged([]);
      toast.success(count === 1 ? 'Sent' : `Sent ${count} files`);
      refreshThread(activeToken);
    },
    onError: (error: Error) => toast.error(error.message || 'Could not send the attachment'),
  });

  const sendVoice = useMutation({
    mutationFn: (payload: { audio_base64: string; mime_type: string; caption?: string }) =>
      api.sendTalkVoice({ token: activeToken, ...payload, file_name: `voice-${Date.now()}.webm`, as_user: asUser }),
    onSuccess: () => {
      toast.success('Voice message sent');
      refreshThread(activeToken);
    },
    onError: (error: Error) => toast.error(error.message || 'Voice message failed'),
  });

  const openCall = () => {
    if (!activeToken) return;
    void startCall(activeToken, activeConversation?.display_name || 'Family call');
  };

  const react = async (message: TalkMessage, emoji: string) => {
    if (typeof message.id !== 'number') return;
    const key = String(message.id);
    const before = reactionOverrides[key] ?? reactionCounts(message.reactions);
    setReactionOverrides((current) => ({ ...current, [key]: { ...before, [emoji]: (before[emoji] || 0) + 1 } }));
    try {
      const res = await api.reactToTalkMessage({ token: activeToken, message_id: message.id, reaction: emoji, as_user: asUser });
      const counts = reactionCounts((res.detail as { reactions?: unknown } | undefined)?.reactions);
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
      className={`grid min-h-0 overflow-hidden rounded-3xl max-lg:flex-1 max-lg:grid-cols-[minmax(0,1fr)] lg:min-h-[440px] border border-white/5 bg-black/20 lg:h-[680px] lg:grid-cols-[320px_1fr] ${className}`}
      data-testid="chat-panel"
    >
      {/* Inbox */}
      <aside className={`min-h-0 min-w-0 flex-col border-white/5 p-3 lg:flex lg:border-r ${showThread ? 'hidden' : 'flex'}`}>
        {conversationsError && conversations.length > 0 && (
          <div className="mb-2 rounded-xl border border-rose-400/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-100" role="status">
            <span className="font-semibold">Couldn't refresh chats.</span>{' '}
            {conversationsErrorMessage}
            <button
              type="button"
              onClick={() => void refetchConversations()}
              disabled={loadingConversations}
              className="ml-2 font-semibold underline underline-offset-2 disabled:opacity-50"
            >
              Retry
            </button>
          </div>
        )}
        {conversationsError && conversations.length === 0 ? (
          <div
            role="alert"
            data-testid="conversations-error"
            className="rounded-2xl border border-rose-400/20 bg-rose-500/10 p-4 text-sm text-rose-100"
          >
            <p className="font-semibold">Couldn't load conversations</p>
            <p className="mt-1 text-xs leading-relaxed text-rose-200/80">{conversationsErrorMessage}</p>
            <button
              type="button"
              data-testid="conversations-retry"
              onClick={() => void refetchConversations()}
              disabled={loadingConversations}
              className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-xl bg-rose-500/20 px-3 font-medium text-rose-100 hover:bg-rose-500/30 disabled:opacity-50"
            >
              {loadingConversations ? 'Retrying…' : 'Try again'}
            </button>
          </div>
        ) : (
          <ConversationList
            conversations={conversations}
            activeToken={activeToken}
            myActorIds={myActorIds}
            loading={loadingConversations}
            opening={openConversation.isPending}
            onSelect={select}
            onNewChat={() => setNewChatOpen(true)}
            defaultToken={defaultToken}
            people={people}
          />
        )}
      </aside>

      {/* Thread */}
      <section
        ref={threadRef}
        style={keyboardHeight ? { height: keyboardHeight, minHeight: 0 } : undefined}
        // On a phone an open conversation is the whole screen, over the app's
        // header and tab bar, the way every messaging app shows one.
        className={`relative min-h-0 min-w-0 flex-col lg:flex ${
          showThread
            ? 'flex max-lg:fixed max-lg:inset-x-0 max-lg:top-0 max-lg:z-[70] max-lg:h-[100dvh] max-lg:bg-slate-950 max-lg:pt-[env(safe-area-inset-top)] max-lg:pb-[env(safe-area-inset-bottom)]'
            : 'hidden'
        }`}
        data-testid="chat-thread"
        onDragOver={(event) => {
          if (!activeToken || !event.dataTransfer.types.includes('Files')) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget === event.target) setDragging(false);
        }}
        onDrop={(event) => {
          if (!event.dataTransfer.files.length) return;
          event.preventDefault();
          setDragging(false);
          const files = Array.from(event.dataTransfer.files).filter((f) => f.size <= MAX_ATTACHMENT_BYTES);
          if (files.length < event.dataTransfer.files.length) toast.error('Files over 25 MB were left out');
          setStaged((current) => [...current, ...files]);
        }}
      >
        {dragging && (
          <div className="pointer-events-none absolute inset-2 z-30 flex items-center justify-center rounded-3xl border-2 border-dashed border-purple-400/70 bg-purple-500/15 backdrop-blur-sm">
            <p className="flex items-center gap-2 text-sm font-semibold text-purple-100">
              <ImageIcon size={18} /> Drop to send to {name}
            </p>
          </div>
        )}

        <header className="flex shrink-0 items-center gap-2 border-b border-white/5 bg-slate-900/95 px-2 py-2.5 sm:px-3">
          <button
            type="button"
            className="flex min-h-10 min-w-10 items-center justify-center rounded-full text-slate-200 hover:bg-white/10 lg:hidden"
            aria-label="Back to chats"
            onClick={() => setMobileView('list')}
          >
            <ChevronLeft size={24} />
          </button>
          <button
            type="button"
            onClick={() => activeConversation && setInfoOpen(true)}
            disabled={!activeConversation}
            aria-label={`About ${name}`}
            data-testid="chat-info-open"
            className="flex min-w-0 flex-1 items-center gap-3 rounded-xl text-left"
          >
            {activeConversation && <ConversationAvatar conversation={activeConversation} people={people} size={42} />}
            <div className="min-w-0 flex-1">
              <p className="truncate text-[16px] font-bold text-white">{name}</p>
              <p className={`truncate text-[12px] ${jarvisThinking ? 'text-fuchsia-300' : 'text-slate-400'}`}>
                {jarvisThinking
                  ? 'Jarvis is thinking…'
                  : activeConversation?.description || (isGroup ? 'Family group · @Jarvis to ask' : 'Direct message')}
              </p>
            </div>
          </button>
          {activeToken && (
            <button
              type="button"
              className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/[0.07] text-slate-200 hover:bg-white/[0.12]"
              onClick={openCall}
              aria-label="Drop in to the family call"
              title="Drop in to the family call"
            >
              <Phone size={17} />
            </button>
          )}
          {activeConversation && (
            <button
              type="button"
              className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/[0.07] text-slate-200 hover:bg-white/[0.12]"
              aria-label="Conversation details"
              onClick={() => setInfoOpen(true)}
            >
              <Info size={17} />
            </button>
          )}
        </header>

        <VerseBanner />

        <PollStrip polls={polls} onVote={(pollId, optionId) => void vote(pollId, optionId)} />

        <MessageThread
          // Remount when the thread comes on screen: on a phone it is laid out
          // hidden behind the list, where "open at the latest message" measured
          // nothing and left the first conversation opened at its oldest.
          key={onScreenToken ? activeToken : `${activeToken}:hidden`}
          messages={messages}
          myActorIds={myActorIds}
          isGroup={isGroup}
          people={people}
          lastCommonRead={activeConversation?.last_common_read}
          unreadAfter={unreadMarkers[activeToken]}
          jarvisThinking={jarvisThinking}
          reactionCounts={(message) =>
            (typeof message.id === 'number' && reactionOverrides[String(message.id)]) || reactionCounts(message.reactions)
          }
          onReact={(message, emoji) => void react(message, emoji)}
          onReply={(message) => {
            setEditing(null);
            setReplyTo(message);
          }}
          onEdit={(message) => {
            setReplyTo(null);
            setEditing(message);
          }}
          onDelete={(message) => void remove(message)}
          onRetry={(message) => void deliver(activeToken, message)}
          onDiscard={(message) => updatePending(activeToken, (list) => list.filter((m) => m.id !== message.id))}
          onLoadEarlier={
            loadingEarlier || (fetched.length >= limit && limit < MAX_HISTORY)
              ? () => setHistoryLimit((current) => ({ ...current, [activeToken]: Math.min(limit + PAGE, MAX_HISTORY) }))
              : undefined
          }
          loadingEarlier={loadingEarlier}
          emptyText={activeToken ? 'No messages yet. Say hello, or ask @Jarvis something.' : 'Pick a conversation to see messages.'}
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
          token={activeToken}
          disabled={!activeToken}
          replyTo={replyTo}
          onCancelReply={() => setReplyTo(null)}
          editing={editing}
          onCancelEdit={() => setEditing(null)}
          onSaveEdit={(text) => void saveEdit(text)}
          onSend={send}
          quickActions={[
            {
              key: 'verse',
              label: 'Verse',
              icon: <BookOpen size={14} className="text-sky-300" />,
              // Share today's verse with everyone in the conversation.
              onSelect: () =>
                void queryClient
                  .fetchQuery({ queryKey: ['bible-verse-of-day'], queryFn: () => api.getBibleVerseOfDay(), staleTime: 60 * 60 * 1000 })
                  .then((v) => {
                    if (v && 'reference' in v && v.reference) send(verseShareText(v.reference, v.text, v.version));
                    else toast.error("Today's verse is not available");
                  })
                  .catch(() => toast.error("Today's verse is not available")),
            },
            ...(onOpenTab
              ? [
                  { key: 'games', label: 'Games', icon: <Gamepad2 size={14} className="text-emerald-300" />, onSelect: () => onOpenTab('games') },
                  { key: 'create', label: 'Create', icon: <Brush size={14} className="text-pink-300" />, onSelect: () => onOpenTab('create') },
                ]
              : []),
          ]}
          onSendVoice={async (payload) => {
            try {
              await sendVoice.mutateAsync(payload);
              return true;
            } catch {
              return false;
            }
          }}
          sendingVoice={sendVoice.isPending}
          staged={staged}
          onStage={(files) => setStaged((current) => [...current, ...files])}
          onUnstage={(index) => setStaged((current) => current.filter((_, i) => i !== index))}
          onSendFiles={async (caption) => {
            try {
              await sendFiles.mutateAsync(caption);
              return true;
            } catch {
              return false;
            }
          }}
          sendingFiles={sendFiles.isPending}
          onNewPoll={() => setShowPollForm(true)}
          isAdmin={isAdmin}
          sendAs={sendAs}
          onSendAsChange={setSendAs}
        />
      </section>

      {infoOpen && activeConversation && (
        <ConversationInfo
          conversation={activeConversation}
          myActorIds={myActorIds}
          isDefault={defaultToken === activeConversation.token}
          onToggleDefault={() => setDefault.mutate(defaultToken === activeConversation.token ? null : activeConversation.token)}
          onGone={() => {
            setInfoOpen(false);
            setSelectedToken('');
            setMobileView('list');
          }}
          onClose={() => setInfoOpen(false)}
        />
      )}
      {newChatOpen && (
        <NewChatSheet
          me={[...myActorIds]}
          busy={openConversation.isPending || createGroup.isPending}
          onDirect={(person) => {
            setNewChatOpen(false);
            openConversation.mutate(person.id);
          }}
          onGroup={(name, people) => createGroup.mutate({ name, people: people.map((p) => p.id) })}
          onClose={() => setNewChatOpen(false)}
        />
      )}
    </div>
  );
}
