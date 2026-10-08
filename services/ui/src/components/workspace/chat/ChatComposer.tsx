import { useEffect, useRef, useState } from 'react';
import { CornerDownLeft, ListPlus, Send, Square, X } from 'lucide-react';
import type { WorkspaceAskMode } from '../../../types/api';
import { ASK_MODES, askModeMeta } from './askModes';

interface ChatComposerProps {
  onSend: (text: string, mode: WorkspaceAskMode) => void;
  /** A turn is running: Enter queues the message (Pi's "follow-up") instead. */
  running?: boolean;
  onStop?: () => void;
  queued?: string[];
  onUnqueue?: (index: number) => void;
  initialMode?: WorkspaceAskMode;
  autoFocus?: boolean;
  compact?: boolean;
}

const MAX_HEIGHT_PX = 180;

/**
 * The one composer for workspace chats: starting a chat from the sidebar and
 * replying inside a chat tab. Enter sends, Shift+Enter adds a line; while a
 * turn runs, Enter queues the message and it is sent when the turn finishes.
 */
export default function ChatComposer({
  onSend,
  running = false,
  onStop,
  queued = [],
  onUnqueue,
  initialMode = 'auto',
  autoFocus = false,
  compact = false,
}: ChatComposerProps) {
  const [text, setText] = useState('');
  const [mode, setMode] = useState<WorkspaceAskMode>(initialMode);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [text]);

  const submit = () => {
    const value = text.trim();
    if (!value) return;
    onSend(value, mode);
    setText('');
  };

  return (
    <div className="space-y-2" data-testid="chat-composer">
      {queued.length > 0 && (
        <ul className="space-y-1" aria-label="Queued messages">
          {queued.map((q, i) => (
            <li key={`${i}-${q}`} className="flex items-center gap-2 rounded-lg border border-dashed border-white/15 bg-white/5 px-2 py-1 text-[11px] text-slate-300">
              <ListPlus size={12} className="shrink-0 text-slate-500" />
              <span className="flex-1 truncate">{q}</span>
              <span className="shrink-0 text-[10px] text-slate-500">sends after this turn</span>
              {onUnqueue && (
                <button type="button" aria-label="Remove queued message" onClick={() => onUnqueue(i)} className="text-slate-500 hover:text-slate-200">
                  <X size={12} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <div role="group" aria-label="Response mode" className="flex gap-1 rounded-lg bg-black/40 p-0.5">
        {ASK_MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            onClick={() => setMode(m.id)}
            aria-pressed={mode === m.id}
            title={m.blurb}
            className={`flex-1 rounded-md px-2 text-[11px] font-medium transition-colors ${compact ? 'min-h-8' : 'min-h-9'} ${
              mode === m.id ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-white/10 hover:text-slate-200'
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className="flex items-end gap-2">
        <textarea
          ref={inputRef}
          value={text}
          autoFocus={autoFocus}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
            if (e.key === 'Escape' && running && onStop) onStop();
          }}
          aria-label="Workspace prompt"
          rows={compact ? 2 : 1}
          placeholder={running ? 'Type to queue a follow-up…' : askModeMeta(mode).placeholder}
          className="min-h-10 flex-1 resize-none rounded-xl border border-white/10 bg-black/50 px-3 py-2 text-sm text-white placeholder-slate-600 outline-none focus:border-indigo-500"
        />
        {running && onStop && (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop"
            title="Stop (Esc)"
            className="flex min-h-10 min-w-10 items-center justify-center rounded-xl bg-rose-600/80 text-white hover:bg-rose-500"
          >
            <Square size={14} />
          </button>
        )}
        <button
          type="button"
          onClick={submit}
          disabled={!text.trim()}
          aria-label={running ? 'Queue message' : `Send as ${askModeMeta(mode).label}`}
          className="flex min-h-10 min-w-10 items-center justify-center rounded-xl bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40"
        >
          {running ? <ListPlus size={15} /> : <Send size={15} />}
        </button>
      </div>
      {!compact && (
        <p className="flex items-center gap-1 text-[10px] text-slate-500">
          <CornerDownLeft size={10} /> Enter to {running ? 'queue' : 'send'} · Shift+Enter for a new line{running ? ' · Esc to stop' : ''} · {askModeMeta(mode).blurb}
        </p>
      )}
    </div>
  );
}
