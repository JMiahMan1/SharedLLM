import { useMemo, useState } from 'react';
import { Loader2, PenSquare, Search, X } from 'lucide-react';
import { avatarTint, initials, listTimeLabel, previewLine, type TalkConversation } from './chatModel';

interface ConversationListProps {
  conversations: TalkConversation[];
  activeToken: string;
  myActorIds: ReadonlySet<string>;
  loading: boolean;
  opening: boolean;
  onSelect: (token: string) => void;
  onStart: (username: string) => void;
}

/**
 * The inbox: search on top, newest conversation first, each row showing who
 * said what and when -- the layout WhatsApp, Messenger and iMessage share.
 */
export default function ConversationList({
  conversations,
  activeToken,
  myActorIds,
  loading,
  opening,
  onSelect,
  onStart,
}: ConversationListProps) {
  const [query, setQuery] = useState('');
  const [composing, setComposing] = useState(false);
  const [username, setUsername] = useState('');

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const sorted = [...conversations].sort(
      (a, b) => (b.last_message_timestamp ?? b.last_activity ?? 0) - (a.last_message_timestamp ?? a.last_activity ?? 0),
    );
    if (!q) return sorted;
    return sorted.filter(
      (c) => c.display_name.toLowerCase().includes(q) || (c.last_message || '').toLowerCase().includes(q),
    );
  }, [conversations, query]);

  const start = () => {
    const name = username.trim();
    if (!name) return;
    onStart(name);
    setUsername('');
    setComposing(false);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between px-1 pb-2">
        <h2 className="text-lg font-bold text-slate-100">Chats</h2>
        <button
          type="button"
          className="glass-button min-h-10 min-w-10 p-2"
          aria-label={composing ? 'Cancel new conversation' : 'New conversation'}
          aria-expanded={composing}
          onClick={() => setComposing((v) => !v)}
        >
          {composing ? <X size={16} /> : <PenSquare size={16} />}
        </button>
      </div>

      {composing && (
        <div className="mb-2 flex items-center gap-2">
          <input
            autoFocus
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') start();
              if (event.key === 'Escape') setComposing(false);
            }}
            placeholder="Username to message"
            aria-label="Start a conversation with"
            className="glass-input flex-1 text-base sm:text-sm"
          />
          <button
            type="button"
            className="glass-button min-h-10 px-3 text-sm"
            aria-label="Open conversation"
            disabled={opening || !username.trim()}
            onClick={start}
          >
            {opening ? <Loader2 size={14} className="animate-spin" /> : 'Chat'}
          </button>
        </div>
      )}

      <label className="relative mb-2 block">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search"
          aria-label="Search conversations"
          className="glass-input w-full rounded-full pl-8 text-base sm:text-sm"
        />
      </label>

      <ul className="min-h-0 flex-1 overflow-y-auto overscroll-contain" aria-label="Conversations">
        {visible.map((conversation) => {
          const unread = conversation.unread_messages ?? 0;
          const active = conversation.token === activeToken;
          const name = conversation.display_name || conversation.token;
          const showBadge = unread > 0 && !active;
          return (
            <li key={conversation.token}>
              <button
                type="button"
                onClick={() => onSelect(conversation.token)}
                aria-current={active ? 'true' : undefined}
                className={`flex w-full items-center gap-3 rounded-2xl px-2 py-2.5 text-left transition min-h-16 ${
                  active ? 'bg-purple-500/15' : 'hover:bg-white/5'
                }`}
              >
                <span
                  aria-hidden
                  className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white"
                  style={{ background: avatarTint(name) }}
                >
                  {initials(name)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline gap-2">
                    <span className={`truncate text-[15px] ${showBadge ? 'font-bold text-white' : 'font-semibold text-slate-100'}`}>
                      {name}
                    </span>
                    <span className={`ml-auto shrink-0 text-[11px] ${showBadge ? 'text-purple-300' : 'text-slate-500'}`}>
                      {listTimeLabel(conversation.last_message_timestamp ?? conversation.last_activity)}
                    </span>
                  </span>
                  <span className="mt-0.5 flex items-center gap-2">
                    <span className={`truncate text-[13px] ${showBadge ? 'text-slate-200' : 'text-slate-400'}`}>
                      {previewLine(conversation, myActorIds)}
                    </span>
                    {showBadge && (
                      <span
                        data-testid={`unread-badge-${conversation.token}`}
                        aria-label={`${unread} unread`}
                        className="ml-auto flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-purple-500 px-1.5 text-[11px] font-bold text-white"
                      >
                        {conversation.unread_mention ? '@' : unread}
                      </span>
                    )}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
        {visible.length === 0 && (
          <li className="px-4 py-8 text-center text-sm text-slate-500">
            {loading
              ? 'Loading conversations…'
              : query
                ? `No chats match “${query}”.`
                : 'No conversations yet. Start one with the pencil above.'}
          </li>
        )}
      </ul>
    </div>
  );
}
