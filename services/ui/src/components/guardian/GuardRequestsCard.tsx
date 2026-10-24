import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Inbox } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { requestLine } from '../../lib/guardRequests';
import type { GuardRequest } from '../../types/api';

/**
 * What a child is waiting on (Phase 11 step 5): a newly installed app (closed
 * on their phone until a parent says yes), a permission an app asked for, or
 * a site the web filter blocked. A site can be allowed for an hour or always.
 */
export default function GuardRequestsCard({ username, name }: { username: string; name: string }) {
  const queryClient = useQueryClient();
  const { data, isError } = useQuery({
    queryKey: ['guard-requests', 'pending'],
    queryFn: () => api.getGuardRequests('pending'),
    refetchInterval: 60 * 1000,
    retry: false,
  });
  const decide = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { approve: boolean; minutes?: number; always?: boolean } }) =>
      api.decideGuardRequest(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['guard-requests'] });
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
    },
  });
  const theirs = (data?.requests || []).filter((r) => r.username === username);
  const failure = isAxiosError(decide.error) ? (decide.error.response?.data as { detail?: string })?.detail || 'That did not save.' : null;

  if (isError) {
    return <p className="glass-panel rounded-2xl p-4 text-xs text-amber-300">{name}'s requests could not be loaded.</p>;
  }
  if (theirs.length === 0) return null;

  const button = (r: GuardRequest, label: string, body: { approve: boolean; minutes?: number; always?: boolean }, primary = true) => (
    <button
      type="button"
      disabled={decide.isPending}
      onClick={() => decide.mutate({ id: r.id, body })}
      className={primary ? 'glass-button px-3 py-1.5 text-xs min-h-11' : 'px-3 py-1.5 text-xs min-h-11 text-slate-400'}
    >
      {label}
    </button>
  );

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-2" data-testid="guard-requests">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
        <Inbox size={16} className="text-sky-300" /> {name} is asking
      </h2>
      <ul className="space-y-2">
        {theirs.map((r) => (
          <li key={r.id} className="rounded-xl border border-sky-400/30 bg-sky-400/5 p-3 space-y-2" data-testid={`guard-request-${r.id}`}>
            <p className="text-xs text-slate-200">
              {requestLine(r)}
              {r.note ? `: “${r.note}”` : ''}
            </p>
            <div className="flex flex-wrap gap-2">
              {r.kind === 'site' ? (
                <>
                  {button(r, 'Allow 1 hour', { approve: true, minutes: 60 })}
                  {button(r, 'Always allow', { approve: true, always: true })}
                </>
              ) : (
                button(r, r.kind === 'app' ? 'Approve app' : 'Allow', { approve: true })
              )}
              {button(r, 'No', { approve: false }, false)}
            </div>
          </li>
        ))}
      </ul>
      {failure && <p className="text-xs text-amber-300">{failure}</p>}
    </section>
  );
}
