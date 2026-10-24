import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { PartyPopper } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { localDay } from '../../lib/healthHub';
import { errorText } from '../../lib/errorText';
import { useFamilyDirectory } from '../chat/familyDirectory';

/**
 * Cheers (Phase 7): kind words for a family member's day, from a fixed set,
 * to the people the server says you may cheer (they share health with you,
 * or are in one of this week's challenges with you). Cheers you received
 * today are listed first. A cheer never shows anything about the day itself.
 */
export default function CheersCard() {
  const queryClient = useQueryClient();
  const day = useMemo(() => localDay(), []);
  const lookup = useFamilyDirectory();
  const nameOf = (user: string) => lookup(user)?.display_name || user;
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['health-cheers', day],
    queryFn: () => api.getHealthCheers(day),
    staleTime: 60_000,
  });
  const send = useMutation({
    mutationFn: (body: { to_user: string; kind: string }) => api.sendHealthCheer({ ...body, day }),
    onSuccess: (cheer) => {
      toast.success(cheer.duplicate ? `${nameOf(cheer.to_user)} already has that one` : `${cheer.emoji} sent to ${nameOf(cheer.to_user)}`);
      void queryClient.invalidateQueries({ queryKey: ['health-cheers'] });
    },
    onError: (error) => toast.error(errorText(error, 'Could not send the cheer')),
  });

  if (isLoading) return <div className="h-24 skeleton rounded-2xl" aria-hidden />;
  // Nobody to cheer and nothing received: the card has nothing to say.
  if (data && data.can_cheer.length === 0 && data.received.length === 0) return null;

  return (
    <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="cheers-heading" data-testid="cheers-card">
      <h3 id="cheers-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
        <PartyPopper size={13} className="text-pink-300" />
        Cheers
      </h3>
      {isError || !data ? (
        <div className="mt-2 flex items-center gap-3">
          <p className="text-xs text-amber-300">Cheers could not be loaded.</p>
          <button type="button" onClick={() => void refetch()} className="glass-button min-h-11 px-3 text-xs">
            Try again
          </button>
        </div>
      ) : (
        <div className="mt-2 space-y-3">
          {data.received.length > 0 && (
            <ul className="space-y-1 text-xs text-slate-200" aria-label="Cheers for you today" data-testid="cheers-received">
              {data.received.map((cheer) => (
                <li key={cheer.id}>
                  {cheer.emoji} {nameOf(cheer.from_user)}: {cheer.label}
                </li>
              ))}
            </ul>
          )}
          {data.can_cheer.map((user) => {
            const already = new Set(data.sent.filter((cheer) => cheer.to_user === user).map((cheer) => cheer.kind));
            return (
              <div key={user} data-testid={`cheer-${user}`}>
                <p className="text-[11px] text-slate-400">Cheer {nameOf(user)}&apos;s day</p>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {data.kinds.map((kind) => (
                    <button
                      key={kind.kind}
                      type="button"
                      onClick={() => send.mutate({ to_user: user, kind: kind.kind })}
                      disabled={send.isPending || already.has(kind.kind)}
                      aria-pressed={already.has(kind.kind)}
                      aria-label={`${kind.label} to ${nameOf(user)}`}
                      className="min-h-11 rounded-xl border border-white/10 bg-white/[0.03] px-2.5 text-xs text-slate-200 disabled:opacity-50"
                    >
                      {kind.emoji} {kind.label}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
