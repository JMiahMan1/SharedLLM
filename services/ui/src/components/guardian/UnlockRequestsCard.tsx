import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LockOpen } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { relativeTime } from '../../lib/wanderTrips';
import MemberCard from '../member/MemberCard';
import { UNLOCK_MINUTE_CHOICES, expiresIn, minutesLabel, unlockLine, unlockRequestsQuery } from '../../lib/unlockRequests';
import type { UnlockRequest } from '../../types/api';

function UnlockRow({ request }: { request: UnlockRequest }) {
  const queryClient = useQueryClient();
  const choices = Array.from(new Set([...UNLOCK_MINUTE_CHOICES, request.minutes_asked])).sort((a, b) => a - b);
  const [minutes, setMinutes] = useState(request.minutes_asked);
  const decide = useMutation({
    mutationFn: (body: { approve: boolean; minutes?: number }) => api.decideUnlockRequest(request.id, body),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['unlock-requests'] }),
  });
  const failure = isAxiosError(decide.error)
    ? (decide.error.response?.data as { detail?: string })?.detail || 'That did not save.'
    : decide.error ? 'That did not save.' : null;
  const yes = 'glass-button min-h-11 rounded-xl px-3.5 text-xs font-semibold text-sky-100 border-sky-400/30 bg-sky-500/15 hover:bg-sky-500/25';
  const no = 'min-h-11 rounded-xl border border-white/10 bg-white/5 px-3.5 text-xs font-medium text-slate-300 hover:bg-white/10 hover:text-white transition-colors';

  return (
    <li className="rounded-xl border border-sky-400/30 bg-gradient-to-br from-sky-500/10 to-sky-400/[0.03] p-3.5 space-y-3" data-testid={`unlock-request-${request.id}`}>
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 break-words text-xs font-medium leading-relaxed text-slate-100">
          {unlockLine(request)}
          {request.note ? `: “${request.note}”` : ''}
        </p>
        <span className="shrink-0 rounded-full border border-sky-400/20 bg-sky-400/10 px-2 py-0.5 text-[10px] font-medium text-sky-200">
          {relativeTime(request.created_at)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2" role="radiogroup" aria-label="How long to unlock">
        {choices.map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={m === minutes}
            onClick={() => setMinutes(m)}
            className={`min-h-11 rounded-xl border px-3 text-xs font-medium transition-colors ${
              m === minutes ? 'border-sky-400/40 bg-sky-500/20 text-sky-100' : 'border-white/10 bg-white/5 text-slate-300 hover:bg-white/10'
            }`}
          >
            {minutesLabel(m)}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={yes} disabled={decide.isPending} onClick={() => decide.mutate({ approve: true, minutes })}>
          Approve for {minutesLabel(minutes)}
        </button>
        <button type="button" className={no} disabled={decide.isPending} onClick={() => decide.mutate({ approve: false })}>
          Deny
        </button>
        <span className="text-[11px] text-slate-400">{expiresIn(request.expires_at)}</span>
      </div>
      {failure && <p className="text-xs text-amber-300" role="alert">{failure}</p>}
    </li>
  );
}

/**
 * A locked device of `username` asking a parent to unlock it (their PC or
 * phone): approve for a length the parent picks, or deny. Both parents get
 * the same request as a notification with Approve / Deny; this is the place
 * to give a different length. Nothing shows while nothing is waiting.
 */
export default function UnlockRequestsCard({ username, name }: { username: string; name: string }) {
  const { data, isError } = useQuery(unlockRequestsQuery);
  const rows = (data || []).filter((r) => r.username === username);
  if (isError) {
    return (
      <MemberCard title={`${name} asks to unlock`} icon={LockOpen} tone="text-sky-300" testId="unlock-requests"
        error={`${name}'s unlock requests could not be loaded.`} />
    );
  }
  if (rows.length === 0) return null;
  return (
    <MemberCard title={`${name} asks to unlock`} icon={LockOpen} tone="text-sky-300" testId="unlock-requests" aside={`${rows.length} waiting`}>
      <ul className="space-y-2.5">
        {rows.map((r) => <UnlockRow key={r.id} request={r} />)}
      </ul>
    </MemberCard>
  );
}
