import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { api } from '../../services/api';
import type { NoticeAction } from '../../types/api';

/** Where a `view` button may go: a path inside the app, or a web address with a host (as on the phone). */
function viewTarget(url: unknown): { inApp: boolean; href: string } | null {
  if (typeof url !== 'string') return null;
  if (url.startsWith('/') && !url.startsWith('//')) return { inApp: true, href: url };
  if (/^https:\/\/[^/\s]+/.test(url)) return { inApp: false, href: url };
  return null;
}

function errorText(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' && detail ? detail : 'That did not go through. Try again.';
}

interface Props {
  actions: NoticeAction[];
  /** Opens an in-app path (the bell closes and the app navigates). */
  onOpen: (path: string) => void;
}

/**
 * A notice's buttons in the bell, the same ones the phone shows: a callback answers at once
 * (a Home notice's Confirm reaches the automation waiting for it), a reply asks for text first,
 * and a view opens its link. Each button says when its answer went through, or why it did not.
 */
const NotificationActions = ({ actions, onOpen }: Props) => {
  const [busy, setBusy] = useState<number | null>(null);
  const [done, setDone] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [replying, setReplying] = useState<number | null>(null);
  const [text, setText] = useState('');

  const send = async (index: number, action: NoticeAction, reply?: string) => {
    if (!action.callback) return;
    setBusy(index);
    setError(null);
    try {
      await api.pushAction(action.callback, action.params ?? {}, reply);
      setDone(index);
      setReplying(null);
      setText('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const shown = actions.slice(0, 3);
  if (shown.length === 0) return null;
  if (done !== null) {
    return (
      <p className="mt-2 text-[11px] font-semibold text-emerald-300" role="status" data-testid="notification-action-done">
        Sent: {shown[done]?.label}
      </p>
    );
  }
  const replyAction = replying !== null ? shown[replying] : null;
  return (
    <div className="mt-2" data-testid="notification-actions">
      <div className="flex flex-wrap gap-2">
        {shown.map((action, i) => {
          if (action.type === 'view') {
            const target = viewTarget(action.url);
            if (!target) return null;
            const cls = 'inline-flex min-h-11 items-center rounded-lg bg-white/10 px-3 text-xs font-semibold text-white hover:bg-white/15';
            return target.inApp ? (
              <button key={i} type="button" className={cls} onClick={() => onOpen(target.href)}>{action.label}</button>
            ) : (
              <a key={i} className={cls} href={target.href} target="_blank" rel="noopener noreferrer">{action.label}</a>
            );
          }
          return (
            <button
              key={i}
              type="button"
              disabled={busy !== null}
              aria-pressed={action.type === 'reply' ? replying === i : undefined}
              onClick={() => (action.type === 'reply' ? setReplying(replying === i ? null : i) : void send(i, action))}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-purple-500/20 px-3 text-xs font-semibold text-white hover:bg-purple-500/30 disabled:opacity-60"
            >
              {busy === i && <Loader2 size={12} className="animate-spin" />}
              {action.label}
            </button>
          );
        })}
      </div>
      {replyAction && replying !== null && (
        <form
          className="mt-2 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) void send(replying, replyAction, text.trim());
          }}
        >
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label={replyAction.label}
            placeholder="Type an answer"
            className="glass-input min-h-11 min-w-0 flex-1 px-3 text-xs"
            autoFocus
          />
          <button
            type="submit"
            disabled={!text.trim() || busy !== null}
            className="min-h-11 rounded-lg bg-purple-500/30 px-3 text-xs font-semibold text-white disabled:opacity-60"
          >
            Send
          </button>
        </form>
      )}
      {error && <p className="mt-2 text-[11px] text-rose-300" role="alert">{error}</p>}
    </div>
  );
};

export default NotificationActions;
