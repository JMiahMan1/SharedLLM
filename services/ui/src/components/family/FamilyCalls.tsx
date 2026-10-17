import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, Phone, RefreshCw, Users } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import type { ExecutionResponse, TalkConversation } from '../../types/api';
import { detailList } from '../chat/chatModel';

/** How often the room list refreshes so an active family call appears. */
const REFRESH_MS = 15_000;

/**
 * One-tap family calls.
 *
 * The media itself runs in Nextcloud Talk: clicking a conversation opens its
 * `/call/<token>` page, where the browser (or the Talk phone app) gets the
 * microphone and camera. Talk only lets a logged-in browser session join a
 * call, so the gateway cannot join on the user's behalf; it only supplies the
 * room token and the URL.
 */
export default function FamilyCalls() {
  const [opening, setOpening] = useState<string | null>(null);
  const {
    data: conversations = [],
    isLoading,
    isError,
    refetch,
  } = useQuery<ExecutionResponse, Error, TalkConversation[]>({
    queryKey: ['talk-conversations'],
    queryFn: () => api.getTalkConversations(),
    refetchInterval: REFRESH_MS,
    select: (response) => detailList<TalkConversation>(response, 'conversations').filter((c) => c.token && c.type !== 6),
  });

  const sorted = [...conversations].sort((a, b) => {
    if (Boolean(a.has_call) !== Boolean(b.has_call)) return a.has_call ? -1 : 1;
    if ((a.type === 1) !== (b.type === 1)) return a.type === 1 ? 1 : -1;
    return (b.last_activity ?? 0) - (a.last_activity ?? 0);
  });

  const openCall = async (conversation: TalkConversation) => {
    // Open the tab while the click is still a user gesture. The API call
    // happens after this, and popup blockers would otherwise stop it.
    const tab = window.open('about:blank', '_blank');
    if (tab) tab.opener = null;
    setOpening(conversation.token);
    try {
      const response = await api.getTalkCallLink(conversation.token);
      const url = (response.detail as { url?: unknown } | null | undefined)?.url;
      if (response.status !== 'SUCCESS' || typeof url !== 'string' || !url) {
        throw new Error(response.message || 'Could not open the call.');
      }
      if (tab) tab.location.replace(url);
      else window.location.assign(url);
    } catch (error) {
      tab?.close();
      toast.error(error instanceof Error ? error.message : 'Could not open the call.');
    } finally {
      setOpening(null);
    }
  };

  return (
    <section className="space-y-3" data-testid="family-calls">
      <div className="rounded-2xl border border-white/10 bg-slate-900/70 p-4">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-300">
            <Phone size={18} />
          </span>
          <div className="min-w-0">
            <h2 className="font-semibold text-slate-100">Family calls</h2>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">
              Start a call with the family group or one person. Audio and video run in Nextcloud Talk,
              so the call keeps working when this page is closed.
            </p>
          </div>
        </div>
      </div>

      {isLoading && conversations.length === 0 && (
        <p className="flex items-center gap-2 px-1 text-sm text-slate-400" data-testid="family-calls-loading">
          <Loader2 size={15} className="animate-spin" /> Loading conversations…
        </p>
      )}

      {isError && conversations.length === 0 && (
        <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200" data-testid="family-calls-error">
          <p>Could not load the family conversations.</p>
          <button
            type="button"
            onClick={() => refetch()}
            className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-xl bg-rose-500/20 px-3 font-medium text-rose-100"
          >
            <RefreshCw size={14} /> Try again
          </button>
        </div>
      )}

      {!isLoading && !isError && sorted.length === 0 && (
        <p className="px-1 text-sm text-slate-400" data-testid="family-calls-empty">
          No family conversations yet. Create one in Chat to start calling.
        </p>
      )}

      <div className="grid gap-2 sm:grid-cols-2">
        {sorted.map((conversation) => {
          const group = conversation.type !== 1;
          const Icon = group ? Users : Phone;
          const busy = opening === conversation.token;
          return (
            <div
              key={conversation.token}
              className="flex items-center gap-3 rounded-2xl border border-white/5 bg-slate-900/60 p-3"
              data-testid={`family-call-${conversation.token}`}
            >
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/[0.07] text-slate-200">
                <Icon size={18} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate font-semibold text-slate-100">{conversation.display_name}</p>
                <p className={`truncate text-xs ${conversation.has_call ? 'text-emerald-300' : 'text-slate-400'}`}>
                  {conversation.has_call
                    ? 'Call in progress — join it'
                    : group
                      ? 'Family group · audio and video'
                      : 'Direct call · audio and video'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => openCall(conversation)}
                disabled={busy || (!conversation.has_call && conversation.can_start_call === false)}
                aria-label={conversation.has_call ? `Join the ${conversation.display_name} call` : `Start a call with ${conversation.display_name}`}
                className={`flex min-h-11 shrink-0 items-center gap-2 rounded-xl px-3 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                  conversation.has_call
                    ? 'bg-emerald-500 text-emerald-950 hover:bg-emerald-400'
                    : 'bg-white/[0.08] text-slate-100 hover:bg-white/[0.14]'
                }`}
              >
                {busy ? <Loader2 size={15} className="animate-spin" /> : <Phone size={15} />}
                {busy ? 'Opening…' : conversation.has_call ? 'Join' : 'Call'}
              </button>
            </div>
          );
        })}
      </div>

      <p className="px-1 text-xs leading-relaxed text-slate-500">
        The first time, Talk asks you to sign in and allow the microphone and camera. The ESP32 watch has no
        microphone or speaker, so it can show a call alert but cannot carry the call itself.
      </p>
    </section>
  );
}
