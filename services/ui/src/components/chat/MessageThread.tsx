import { Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowDown, Check, CheckCheck, Copy, CornerUpLeft, Pencil, RotateCcw, SmilePlus, Sparkles, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import EnvelopeBody from './EnvelopeBody';
import {
  avatarTint,
  dayLabel,
  groupIntoRuns,
  initials,
  isEmojiOnly,
  JARVIS_ID,
  plainText,
  QUICK_REACTIONS,
  receiptFor,
  sameDay,
  timeLabel,
  type MessageRun,
  type TalkMessage,
} from './chatModel';

/** How close to the bottom still counts as "reading the latest". */
const AT_BOTTOM_PX = 80;

interface MessageThreadProps {
  messages: TalkMessage[];
  myActorIds: ReadonlySet<string>;
  isGroup: boolean;
  /** Everyone has read up to this id: drives "Read" under your latest message. */
  lastCommonRead?: number;
  /** Your read marker when the conversation was opened: the "New messages" line. */
  unreadAfter?: number;
  jarvisThinking: boolean;
  reactionCounts: (message: TalkMessage) => Record<string, number>;
  onReact: (message: TalkMessage, emoji: string) => void;
  onReply: (message: TalkMessage) => void;
  onEdit: (message: TalkMessage) => void;
  onDelete: (message: TalkMessage) => void;
  onRetry: (message: TalkMessage) => void;
  onDiscard: (message: TalkMessage) => void;
  emptyText: string;
}

export default function MessageThread({
  messages,
  myActorIds,
  isGroup,
  lastCommonRead,
  unreadAfter,
  jarvisThinking,
  reactionCounts,
  onReact,
  onReply,
  onEdit,
  onDelete,
  onRetry,
  onDiscard,
  emptyText,
}: MessageThreadProps) {
  const feedRef = useRef<HTMLDivElement | null>(null);
  const dividerRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);
  const seenCountRef = useRef(0);
  const [unseen, setUnseen] = useState(0);
  const [activeKey, setActiveKey] = useState<string | null>(null);

  const runs = groupIntoRuns(messages, myActorIds);

  // The first message from someone else after your read marker.
  const firstUnreadId =
    unreadAfter === undefined
      ? undefined
      : runs
          .filter((run) => !run.mine && !run.system)
          .flatMap((run) => run.messages)
          .find((m) => typeof m.id === 'number' && m.id > unreadAfter)?.id;

  // "Read"/"Sent" goes under your most recent message only, as in iMessage.
  const mineInOrder = runs.filter((run) => run.mine).flatMap((run) => run.messages);
  const myLatest = mineInOrder[mineInOrder.length - 1];

  const scrollToBottom = (smooth = false) => {
    const feed = feedRef.current;
    if (!feed) return;
    if (typeof feed.scrollTo === 'function') feed.scrollTo({ top: feed.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    else feed.scrollTop = feed.scrollHeight;
    atBottomRef.current = true;
    setUnseen(0);
  };

  // The panel remounts this per conversation (key). It opens at the "New
  // messages" line when there is one, else at the latest message.
  useLayoutEffect(() => {
    const feed = feedRef.current;
    if (!feed) return;
    if (dividerRef.current && typeof dividerRef.current.scrollIntoView === 'function') {
      dividerRef.current.scrollIntoView({ block: 'start' });
    } else {
      feed.scrollTop = feed.scrollHeight;
    }
  }, []);

  // New messages follow you only if you were already at the bottom or sent
  // them yourself; otherwise they wait behind a "N new" pill.
  useEffect(() => {
    const added = messages.length - seenCountRef.current;
    const firstLoad = seenCountRef.current === 0;
    seenCountRef.current = messages.length;
    if (added <= 0 || firstLoad) return;
    const last = messages[messages.length - 1];
    if (atBottomRef.current || last?.pending) scrollToBottom(true);
    else setUnseen((n) => n + added);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on growth
  }, [messages.length]);

  useEffect(() => {
    if (jarvisThinking && atBottomRef.current) scrollToBottom(true);
  }, [jarvisThinking]);

  const onScroll = () => {
    const feed = feedRef.current;
    if (!feed) return;
    atBottomRef.current = feed.scrollHeight - feed.scrollTop - feed.clientHeight < AT_BOTTOM_PX;
    if (atBottomRef.current) setUnseen(0);
  };

  const copy = async (message: TalkMessage) => {
    try {
      await navigator.clipboard.writeText(message.message || '');
      toast.success('Copied');
    } catch {
      toast.error('Could not copy');
    }
  };

  const done = (fn: (message: TalkMessage) => void) => (message: TalkMessage) => {
    setActiveKey(null);
    fn(message);
  };

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={feedRef}
        onScroll={onScroll}
        className="h-full overflow-y-auto overscroll-contain px-3 py-3 sm:px-4"
        data-testid="chat-feed"
        role="log"
        aria-live="polite"
        aria-label="Messages"
      >
        {runs.map((run, index) => {
          const first = run.messages[0];
          const prevRun = runs[index - 1];
          const prevStamp = prevRun?.messages[prevRun.messages.length - 1]?.timestamp;
          const newDay = first.timestamp && (!prevRun || !sameDay(prevStamp, first.timestamp));
          const unreadHere = firstUnreadId !== undefined && run.messages.some((m) => m.id === firstUnreadId);
          return (
            <Fragment key={run.key}>
              {newDay && (
                <div className="my-3 flex justify-center" role="separator">
                  <span className="rounded-full bg-white/5 px-3 py-1 text-[11px] font-medium text-slate-400">
                    {dayLabel(first.timestamp as number)}
                  </span>
                </div>
              )}
              {unreadHere && (
                <div ref={dividerRef} className="my-3 flex items-center gap-3" role="separator" data-testid="unread-divider">
                  <span className="h-px flex-1 bg-purple-400/40" />
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-purple-300">New messages</span>
                  <span className="h-px flex-1 bg-purple-400/40" />
                </div>
              )}
              {run.system ? (
                <p className="my-2 text-center text-[11px] text-slate-500">
                  <EnvelopeBody raw={first.message} fallback={first.system_message} parameters={first.parameters} />
                </p>
              ) : (
                <Run
                  run={run}
                  isGroup={isGroup}
                  myActorIds={myActorIds}
                  activeKey={activeKey}
                  receiptMessage={myLatest}
                  lastCommonRead={lastCommonRead}
                  reactionCounts={reactionCounts}
                  onToggle={(key) => setActiveKey((current) => (current === key ? null : key))}
                  onReact={(message, emoji) => {
                    setActiveKey(null);
                    onReact(message, emoji);
                  }}
                  onReply={done(onReply)}
                  onEdit={done(onEdit)}
                  onDelete={done(onDelete)}
                  onCopy={done((message) => void copy(message))}
                  onRetry={onRetry}
                  onDiscard={onDiscard}
                />
              )}
            </Fragment>
          );
        })}

        {jarvisThinking && (
          <div className="mb-2 flex items-end gap-2" data-testid="jarvis-thinking">
            <JarvisAvatar />
            <div className="rounded-3xl rounded-bl-md border border-fuchsia-400/30 bg-gradient-to-br from-fuchsia-500/15 to-indigo-500/15 px-4 py-3">
              <span className="flex items-center gap-1" aria-hidden>
                {[0, 150, 300].map((delay) => (
                  <span key={delay} className="h-2 w-2 animate-bounce rounded-full bg-fuchsia-300" style={{ animationDelay: `${delay}ms` }} />
                ))}
              </span>
            </div>
            <span className="mb-1 text-[11px] text-slate-500">Jarvis is thinking…</span>
          </div>
        )}

        {messages.length === 0 && !jarvisThinking && <p className="py-10 text-center text-sm text-slate-500">{emptyText}</p>}
      </div>

      {unseen > 0 && (
        <button
          type="button"
          onClick={() => scrollToBottom(true)}
          className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-purple-400/40 bg-slate-900/95 px-3 py-1.5 text-xs font-semibold text-purple-200 shadow-lg"
          aria-label={`${unseen} new message${unseen === 1 ? '' : 's'}, jump to latest`}
        >
          <ArrowDown size={13} /> {unseen} new
        </button>
      )}
    </div>
  );
}

function JarvisAvatar() {
  return (
    <span
      aria-hidden
      className="mb-5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-fuchsia-500 to-indigo-500 text-white shadow"
    >
      <Sparkles size={14} />
    </span>
  );
}

interface RunProps {
  run: MessageRun;
  isGroup: boolean;
  myActorIds: ReadonlySet<string>;
  activeKey: string | null;
  receiptMessage?: TalkMessage;
  lastCommonRead?: number;
  reactionCounts: (message: TalkMessage) => Record<string, number>;
  onToggle: (key: string) => void;
  onReact: (message: TalkMessage, emoji: string) => void;
  onReply: (message: TalkMessage) => void;
  onEdit: (message: TalkMessage) => void;
  onDelete: (message: TalkMessage) => void;
  onCopy: (message: TalkMessage) => void;
  onRetry: (message: TalkMessage) => void;
  onDiscard: (message: TalkMessage) => void;
}

function messageKey(message: TalkMessage, index: number): string {
  return String(message.id ?? `${message.timestamp}-${index}`);
}

function Run({
  run,
  isGroup,
  myActorIds,
  activeKey,
  receiptMessage,
  lastCommonRead,
  reactionCounts,
  onToggle,
  onReact,
  onReply,
  onEdit,
  onDelete,
  onCopy,
  onRetry,
  onDiscard,
}: RunProps) {
  const { mine } = run;
  const jarvis = run.authorId === JARVIS_ID;
  const showName = !mine && (isGroup || jarvis);
  const lastIndex = run.messages.length - 1;
  const last = run.messages[lastIndex];
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  return (
    <div className={`mb-2 flex items-end gap-2 ${mine ? 'justify-end' : 'justify-start'}`}>
      {!mine &&
        (jarvis ? (
          <JarvisAvatar />
        ) : (
          <span
            aria-hidden
            className="mb-5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white"
            style={{ background: avatarTint(run.author) }}
          >
            {initials(run.author)}
          </span>
        ))}
      <div className={`flex min-w-0 max-w-[82%] flex-col sm:max-w-[70%] ${mine ? 'items-end' : 'items-start'}`}>
        {showName && (
          <span className={`mb-0.5 ml-3 inline-flex items-center gap-1 text-[11px] font-semibold ${jarvis ? 'text-fuchsia-300' : 'text-slate-400'}`}>
            {run.author}
            {jarvis && <span className="rounded-full bg-fuchsia-500/20 px-1.5 text-[9px] uppercase tracking-wider">AI</span>}
          </span>
        )}
        {run.messages.map((message, index) => {
          const key = messageKey(message, index);
          const isLast = index === lastIndex;
          const counts = reactionCounts(message);
          const hasReactions = Object.keys(counts).length > 0;
          const hasFile = Object.values(message.parameters || {}).some((p) => p.type === 'file');
          const emojiOnly = isEmojiOnly(message.message) && !message.parent && !hasFile;
          const canAct = typeof message.id === 'number' && !message.pending && !message.deleted;
          const canChange = canAct && mine && !hasFile;
          const corner = mine ? (isLast ? 'rounded-br-md' : 'rounded-br-lg') : isLast ? 'rounded-bl-md' : 'rounded-bl-lg';
          const tone = message.deleted
            ? 'border border-dashed border-white/15 bg-transparent italic text-slate-500'
            : mine
              ? message.pending === 'failed'
                ? 'bg-rose-500/20 text-slate-100'
                : 'bg-gradient-to-br from-purple-600 to-violet-600 text-white'
              : jarvis
                ? 'border border-fuchsia-400/30 bg-gradient-to-br from-fuchsia-500/15 to-indigo-500/15 text-slate-100'
                : 'bg-white/10 text-slate-100';
          const open = activeKey === key;
          return (
            <div key={key} className={`group relative flex w-full flex-col ${mine ? 'items-end' : 'items-start'} ${index > 0 ? 'mt-0.5' : ''}`}>
              <div className={`flex max-w-full items-center gap-1 ${mine ? 'flex-row-reverse' : ''}`}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => canAct && onToggle(key)}
                  onKeyDown={(event) => {
                    if ((event.key === 'Enter' || event.key === ' ') && canAct && event.target === event.currentTarget) {
                      event.preventDefault();
                      onToggle(key);
                    }
                  }}
                  title="Tap to react"
                  className={
                    emojiOnly
                      ? 'cursor-pointer select-text text-4xl leading-tight'
                      : `max-w-full cursor-pointer select-text whitespace-pre-wrap break-words rounded-3xl px-3.5 py-2 text-left text-[15px] leading-snug ${corner} ${tone} ${
                          message.pending === 'sending' ? 'opacity-70' : ''
                        } ${open ? 'ring-2 ring-purple-400/50' : ''}`
                  }
                >
                  {message.parent && (
                    <span
                      className={`mb-1.5 block rounded-xl border-l-2 px-2 py-1 text-xs ${
                        mine ? 'border-white/60 bg-black/15 text-white/80' : 'border-purple-400 bg-black/20 text-slate-300'
                      }`}
                    >
                      <span className="block font-semibold">{message.parent.actor_display_name}</span>
                      <span className="line-clamp-2">{plainText(message.parent.message)}</span>
                    </span>
                  )}
                  <EnvelopeBody
                    raw={message.message}
                    fallback={message.system_message}
                    parameters={message.parameters}
                    myActorIds={myActorIds}
                    voice={message.message_type === 'voice-message'}
                  />
                  {message.last_edit_time && !message.deleted && <span className="ml-1.5 text-[10px] opacity-60">(edited)</span>}
                </div>
                {canAct && (
                  <span className="hidden shrink-0 items-center gap-0.5 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100 md:flex">
                    <IconAction label="React" onClick={() => onToggle(key)}><SmilePlus size={15} /></IconAction>
                    <IconAction label="Reply" onClick={() => onReply(message)}><CornerUpLeft size={15} /></IconAction>
                  </span>
                )}
              </div>

              {hasReactions && (
                <div className={`-mt-1.5 flex flex-wrap gap-1 px-2 ${mine ? 'justify-end' : ''}`} data-testid="reaction-chips">
                  {Object.entries(counts).map(([emoji, count]) => (
                    <button
                      key={emoji}
                      type="button"
                      onClick={() => canAct && onReact(message, emoji)}
                      aria-label={`${emoji} ${count}`}
                      className={`rounded-full border px-1.5 py-0.5 text-[12px] shadow ${
                        message.reactions_self?.includes(emoji) ? 'border-purple-400/60 bg-purple-500/30' : 'border-white/10 bg-slate-800'
                      }`}
                    >
                      {emoji}
                      {count > 1 ? ` ${count}` : ''}
                    </button>
                  ))}
                </div>
              )}

              {open && (
                <div
                  className={`z-10 mt-1 flex flex-wrap items-center gap-1 rounded-3xl border border-white/10 bg-slate-900/95 p-1 shadow-xl ${mine ? 'self-end' : 'self-start'}`}
                  data-testid="reaction-bar"
                >
                  {QUICK_REACTIONS.map((emoji) => (
                    <button
                      key={emoji}
                      type="button"
                      aria-label={`React ${emoji}`}
                      className="min-h-10 min-w-10 rounded-full text-xl transition hover:scale-125 hover:bg-white/10"
                      onClick={() => onReact(message, emoji)}
                    >
                      {emoji}
                    </button>
                  ))}
                  <span className="mx-0.5 h-6 w-px bg-white/10" aria-hidden />
                  <IconAction label="Reply" onClick={() => onReply(message)}><CornerUpLeft size={16} /></IconAction>
                  <IconAction label="Copy" onClick={() => onCopy(message)}><Copy size={16} /></IconAction>
                  {canChange && (
                    <>
                      <IconAction label="Edit" onClick={() => onEdit(message)}><Pencil size={16} /></IconAction>
                      <IconAction label="Delete" onClick={() => setConfirmDelete(key)}><Trash2 size={16} /></IconAction>
                    </>
                  )}
                </div>
              )}

              {confirmDelete === key && (
                <div className="mt-1 flex items-center gap-2 rounded-full border border-rose-400/30 bg-rose-500/10 px-3 py-1 text-xs text-rose-200" role="alert">
                  Delete for everyone?
                  <button
                    type="button"
                    className="font-semibold underline"
                    onClick={() => {
                      setConfirmDelete(null);
                      onDelete(message);
                    }}
                  >
                    Delete
                  </button>
                  <button type="button" className="text-slate-300" onClick={() => setConfirmDelete(null)}>
                    Cancel
                  </button>
                </div>
              )}

              {message.pending === 'failed' && (
                <span className="mt-0.5 flex items-center gap-2 text-[11px] text-rose-300">
                  Not sent
                  <button type="button" className="inline-flex items-center gap-1 underline" onClick={() => onRetry(message)}>
                    <RotateCcw size={11} /> Retry
                  </button>
                  <button type="button" aria-label="Discard unsent message" onClick={() => onDiscard(message)}>
                    <Trash2 size={11} />
                  </button>
                </span>
              )}
            </div>
          );
        })}
        <span className={`mt-0.5 flex items-center gap-1.5 px-2 text-[10px] text-slate-500 ${mine ? 'justify-end' : ''}`}>
          {last.timestamp && last.pending !== 'sending' ? timeLabel(last.timestamp) : ''}
          {mine && receiptMessage && run.messages.includes(receiptMessage) && (
            <Receipt state={receiptFor(receiptMessage, lastCommonRead)} />
          )}
        </span>
      </div>
    </div>
  );
}

function Receipt({ state }: { state: ReturnType<typeof receiptFor> }) {
  if (state === 'failed') return null;
  if (state === 'sending') return <span data-testid="receipt">Sending…</span>;
  if (state === 'read')
    return (
      <span className="inline-flex items-center gap-0.5 font-semibold text-purple-300" data-testid="receipt">
        <CheckCheck size={12} /> Read
      </span>
    );
  return (
    <span className="inline-flex items-center gap-0.5" data-testid="receipt">
      <Check size={12} /> Sent
    </span>
  );
}

function IconAction({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="flex min-h-9 min-w-9 items-center justify-center rounded-full text-slate-400 hover:bg-white/10 hover:text-slate-100"
    >
      {children}
    </button>
  );
}
