import { Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowDown, Copy, CornerUpLeft, RotateCcw, SmilePlus, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import EnvelopeBody from './EnvelopeBody';
import {
  avatarTint,
  dayLabel,
  groupIntoRuns,
  initials,
  isEmojiOnly,
  QUICK_REACTIONS,
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
  reactionCounts: (message: TalkMessage) => Record<string, number>;
  onReact: (message: TalkMessage, emoji: string) => void;
  onReply: (message: TalkMessage) => void;
  onRetry: (message: TalkMessage) => void;
  onDiscard: (message: TalkMessage) => void;
  emptyText: string;
}

export default function MessageThread({
  messages,
  myActorIds,
  isGroup,
  reactionCounts,
  onReact,
  onReply,
  onRetry,
  onDiscard,
  emptyText,
}: MessageThreadProps) {
  const feedRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);
  const seenCountRef = useRef(0);
  const [unseen, setUnseen] = useState(0);
  const [reactingFor, setReactingFor] = useState<string | null>(null);

  const runs = groupIntoRuns(messages, myActorIds);

  const scrollToBottom = (smooth = false) => {
    const feed = feedRef.current;
    if (!feed) return;
    if (typeof feed.scrollTo === 'function') {
      feed.scrollTo({ top: feed.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    } else {
      feed.scrollTop = feed.scrollHeight;
    }
    atBottomRef.current = true;
    setUnseen(0);
  };

  // The panel remounts this per conversation (key), so each one opens at its
  // latest message with fresh state.
  useLayoutEffect(() => {
    const feed = feedRef.current;
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, []);

  // New messages follow you only if you were already at the bottom or sent
  // them yourself; otherwise they wait behind a "new messages" pill instead
  // of yanking you away from what you were reading.
  useEffect(() => {
    const added = messages.length - seenCountRef.current;
    seenCountRef.current = messages.length;
    if (added <= 0) return;
    const last = messages[messages.length - 1];
    if (atBottomRef.current || last?.pending) {
      scrollToBottom(true);
    } else {
      setUnseen((n) => n + added);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on growth
  }, [messages.length]);

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
          return (
            <Fragment key={run.key}>
              {newDay && (
                <div className="my-3 flex justify-center" role="separator">
                  <span className="rounded-full bg-white/5 px-3 py-1 text-[11px] font-medium text-slate-400">
                    {dayLabel(first.timestamp as number)}
                  </span>
                </div>
              )}
              {run.system ? (
                <p className="my-2 text-center text-[11px] text-slate-500">
                  {first.system_message ? <EnvelopeBody raw={first.message} fallback={first.system_message} /> : null}
                </p>
              ) : (
                <Run
                  run={run}
                  isGroup={isGroup}
                  reactingFor={reactingFor}
                  reactionCounts={reactionCounts}
                  onToggleReact={(key) => setReactingFor((current) => (current === key ? null : key))}
                  onReact={(message, emoji) => {
                    setReactingFor(null);
                    onReact(message, emoji);
                  }}
                  onReply={(message) => {
                    setReactingFor(null);
                    onReply(message);
                  }}
                  onCopy={copy}
                  onRetry={onRetry}
                  onDiscard={onDiscard}
                />
              )}
            </Fragment>
          );
        })}
        {messages.length === 0 && <p className="py-10 text-center text-sm text-slate-500">{emptyText}</p>}
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

interface RunProps {
  run: MessageRun;
  isGroup: boolean;
  reactingFor: string | null;
  reactionCounts: (message: TalkMessage) => Record<string, number>;
  onToggleReact: (key: string) => void;
  onReact: (message: TalkMessage, emoji: string) => void;
  onReply: (message: TalkMessage) => void;
  onCopy: (message: TalkMessage) => void;
  onRetry: (message: TalkMessage) => void;
  onDiscard: (message: TalkMessage) => void;
}

function messageKey(message: TalkMessage, index: number): string {
  return String(message.id ?? `${message.timestamp}-${index}`);
}

function Run({ run, isGroup, reactingFor, reactionCounts, onToggleReact, onReact, onReply, onCopy, onRetry, onDiscard }: RunProps) {
  const { mine } = run;
  const showName = !mine && isGroup;
  const lastIndex = run.messages.length - 1;
  const lastStamp = run.messages[lastIndex].timestamp;

  return (
    <div className={`mb-2 flex items-end gap-2 ${mine ? 'justify-end' : 'justify-start'}`}>
      {!mine && (
        <span
          aria-hidden
          className="mb-5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white"
          style={{ background: avatarTint(run.author) }}
        >
          {initials(run.author)}
        </span>
      )}
      <div className={`flex min-w-0 max-w-[80%] flex-col sm:max-w-[70%] ${mine ? 'items-end' : 'items-start'}`}>
        {showName && <span className="mb-0.5 ml-3 text-[11px] font-semibold text-slate-400">{run.author}</span>}
        {run.messages.map((message, index) => {
          const key = messageKey(message, index);
          const isLast = index === lastIndex;
          const counts = reactionCounts(message);
          const hasReactions = Object.keys(counts).length > 0;
          const emojiOnly = isEmojiOnly(message.message) && !message.parent;
          const canAct = typeof message.id === 'number' && !message.pending;
          const corner = mine
            ? isLast ? 'rounded-br-md' : 'rounded-br-lg'
            : isLast ? 'rounded-bl-md' : 'rounded-bl-lg';
          return (
            <div key={key} className={`group relative flex w-full flex-col ${mine ? 'items-end' : 'items-start'} ${index > 0 ? 'mt-0.5' : ''}`}>
              <div className={`flex max-w-full items-center gap-1 ${mine ? 'flex-row-reverse' : ''}`}>
                <button
                  type="button"
                  onClick={() => canAct && onToggleReact(key)}
                  title="Tap to react"
                  className={
                    emojiOnly
                      ? 'text-4xl leading-tight'
                      : `max-w-full rounded-3xl px-3.5 py-2 text-left text-[15px] leading-snug break-words whitespace-pre-wrap ${corner} ${
                          mine
                            ? message.pending === 'failed'
                              ? 'bg-rose-500/20 text-slate-100'
                              : 'bg-purple-600 text-white'
                            : 'bg-white/10 text-slate-100'
                        } ${message.pending === 'sending' ? 'opacity-70' : ''}`
                  }
                >
                  {message.parent && (
                    <span
                      className={`mb-1.5 block rounded-xl border-l-2 px-2 py-1 text-xs ${
                        mine ? 'border-white/60 bg-black/15 text-white/80' : 'border-purple-400 bg-black/20 text-slate-300'
                      }`}
                    >
                      <span className="block font-semibold">{message.parent.actor_display_name}</span>
                      <span className="line-clamp-2">{message.parent.message}</span>
                    </span>
                  )}
                  <EnvelopeBody raw={message.message} fallback={message.system_message} />
                </button>
                {canAct && (
                  <span className="hidden shrink-0 items-center gap-0.5 opacity-0 transition group-hover:opacity-100 focus-within:opacity-100 md:flex">
                    <IconAction label="React" onClick={() => onToggleReact(key)}><SmilePlus size={15} /></IconAction>
                    <IconAction label="Reply" onClick={() => onReply(message)}><CornerUpLeft size={15} /></IconAction>
                    <IconAction label="Copy" onClick={() => onCopy(message)}><Copy size={15} /></IconAction>
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
                        message.reactions_self?.includes(emoji)
                          ? 'border-purple-400/60 bg-purple-500/30'
                          : 'border-white/10 bg-slate-800'
                      }`}
                    >
                      {emoji}
                      {count > 1 ? ` ${count}` : ''}
                    </button>
                  ))}
                </div>
              )}

              {reactingFor === key && (
                <div
                  className={`mt-1 flex items-center gap-1 rounded-full border border-white/10 bg-slate-900/95 p-1 shadow-lg ${mine ? 'self-end' : 'self-start'}`}
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
        <span className={`mt-0.5 px-2 text-[10px] text-slate-500 ${mine ? 'text-right' : ''}`}>
          {run.messages[lastIndex].pending === 'sending' ? 'Sending…' : lastStamp ? timeLabel(lastStamp) : ''}
        </span>
      </div>
    </div>
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
