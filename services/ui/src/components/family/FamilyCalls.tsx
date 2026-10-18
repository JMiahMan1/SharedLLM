import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, Phone, RefreshCw, Users } from 'lucide-react';
import { api } from '../../services/api';
import type { ExecutionResponse, TalkConversation } from '../../types/api';
import { useCall } from '../../context/CallContext';
import { detailList } from '../chat/chatModel';

/** How often the room list refreshes so new conversations appear. */
const REFRESH_MS = 15_000;

/**
 * One-tap family calls, entirely inside Jarvis.
 *
 * The conversation list still comes from Nextcloud Talk because that is where
 * the family's rooms live, but the call itself is a peer-to-peer WebRTC call
 * signalled by the Jarvis gateway. A room token is only the shared name both
 * people join; no Nextcloud call is joined or opened in another tab.
 */
export default function FamilyCalls() {
  const { call, activeCalls, startCall } = useCall();
  const [starting, setStarting] = useState<string | null>(null);
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

  const activeByToken = new Map(activeCalls.map((active) => [active.token, active]));
  const sorted = [...conversations].sort((a, b) => {
    const activeA = activeByToken.has(a.token);
    const activeB = activeByToken.has(b.token);
    if (activeA !== activeB) return activeA ? -1 : 1;
    if ((a.type === 1) !== (b.type === 1)) return a.type === 1 ? 1 : -1;
    return (b.last_activity ?? 0) - (a.last_activity ?? 0);
  });

  const start = async (conversation: TalkConversation) => {
    setStarting(conversation.token);
    try {
      await startCall(conversation.token, conversation.display_name);
    } finally {
      setStarting(null);
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
              Start or join a call with the family group or one person. The call opens inside Jarvis,
              and you can keep using the rest of the app while you talk.
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
          const active = activeByToken.get(conversation.token);
          const inThisCall = call?.room === conversation.token;
          const busy = starting === conversation.token;
          const people = active?.participants.map((person) => person.name).join(', ');
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
                <p className={`truncate text-xs ${active ? 'text-emerald-300' : 'text-slate-400'}`}>
                  {inThisCall
                    ? 'You are in this call'
                    : active
                      ? `${people || 'Someone'} in the call`
                      : group
                        ? 'Family group · audio and video'
                        : 'Direct call · audio and video'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void start(conversation)}
                disabled={busy || (!active && conversation.can_start_call === false)}
                aria-label={
                  inThisCall
                    ? `Show the ${conversation.display_name} call`
                    : active
                      ? `Join the ${conversation.display_name} call`
                      : `Start a call with ${conversation.display_name}`
                }
                className={`flex min-h-11 shrink-0 items-center gap-2 rounded-xl px-3 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                  active
                    ? 'bg-emerald-500 text-emerald-950 hover:bg-emerald-400'
                    : 'bg-white/[0.08] text-slate-100 hover:bg-white/[0.14]'
                }`}
              >
                {busy ? <Loader2 size={15} className="animate-spin" /> : <Phone size={15} />}
                {busy ? 'Starting…' : inThisCall ? 'Open' : active ? 'Join' : 'Call'}
              </button>
            </div>
          );
        })}
      </div>

      <p className="px-1 text-xs leading-relaxed text-slate-500">
        Calls use your microphone and optional camera, and nothing is recorded. The current ESP32-S3
        watch has no microphone or speaker, so it cannot carry a call itself; ESP devices with audio
        can join as WebRTC clients.
      </p>
    </section>
  );
}
