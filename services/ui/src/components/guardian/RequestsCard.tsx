import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { History, Inbox } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { requestLine } from '../../lib/guardRequests';
import { relativeTime } from '../../lib/wanderTrips';
import MemberCard from '../member/MemberCard';
import { guardRequestsQuery, timeRequestsQuery } from '../../lib/memberPage';
import type { GuardRequest, ScreenTimeRequest } from '../../types/api';

type Kind = GuardRequest['kind'] | 'time';
type Row =
  | { kind: 'time'; id: number; at: string; request: ScreenTimeRequest }
  | { kind: GuardRequest['kind']; id: number; at: string; request: GuardRequest };

const ALL_KINDS: Kind[] = ['time', 'app', 'permission', 'site'];

function historyLine(row: Row): string {
  if (row.kind === 'time') {
    const r = row.request as ScreenTimeRequest;
    return r.status === 'granted' ? `Asked for ${r.minutes} more minutes: given ${r.granted_minutes}` : `Asked for ${r.minutes} more minutes: not that day`;
  }
  const r = row.request as GuardRequest;
  return `${requestLine(r).replace(' - waiting for your OK', '')}: ${r.status === 'approved' ? 'yes' : 'no'}${r.decided_by ? ` (${r.decided_by})` : ''}`;
}

/**
 * What a child is asking for, answered inline (Phase 11 steps 4 and 5): more
 * screen time, a newly installed app (closed on their phone until a parent
 * says yes), a permission an app asked for, a site the web filter blocked.
 * With `history`, what was asked and answered before instead.
 */
export default function RequestsCard({ username, name, kinds = ALL_KINDS, history = false }: {
  username: string;
  name: string;
  kinds?: Kind[];
  history?: boolean;
}) {
  const queryClient = useQueryClient();
  const wantsTime = kinds.includes('time');
  const wantsGuard = kinds.some((k) => k !== 'time');
  const time = useQuery({ ...timeRequestsQuery(history), enabled: wantsTime });
  const guard = useQuery({ ...guardRequestsQuery(history), enabled: wantsGuard });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['guard-requests'] });
    void queryClient.invalidateQueries({ queryKey: ['screen-time-requests'] });
    void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
  };
  const decideGuard = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { approve: boolean; minutes?: number; always?: boolean } }) => api.decideGuardRequest(id, body),
    onSuccess: refresh,
  });
  const decideTime = useMutation({ mutationFn: ({ id, grant }: { id: number; grant: boolean }) => api.decideScreenTimeRequest(id, grant), onSuccess: refresh });

  const rows: Row[] = [
    ...(wantsTime ? (time.data || []).filter((r) => r.username === username).map((r) => ({ kind: 'time' as const, id: r.id, at: r.created_at, request: r })) : []),
    ...(wantsGuard ? (guard.data || []).filter((r) => r.username === username && kinds.includes(r.kind)).map((r) => ({ kind: r.kind, id: r.id, at: r.created_at, request: r })) : []),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const failed = [decideGuard.error, decideTime.error].find(Boolean);
  const failure = isAxiosError(failed) ? (failed.response?.data as { detail?: string })?.detail || 'That did not save.' : failed ? 'That did not save.' : null;
  const busy = decideGuard.isPending || decideTime.isPending;
  const yes = 'glass-button min-h-11 px-3 text-xs';
  const no = 'min-h-11 px-3 text-xs text-slate-400';

  return (
    <MemberCard
      title={history ? 'Request history' : `${name} is asking`}
      icon={history ? History : Inbox}
      testId={history ? 'request-history' : 'open-requests'}
      isLoading={(wantsTime && time.isLoading) || (wantsGuard && guard.isLoading)}
      error={time.isError || guard.isError ? `${name}'s requests could not be loaded.` : null}
      empty={rows.length === 0 ? (history ? 'No answered requests yet.' : 'Nothing waiting for an answer.') : null}
    >
      <ul className={`space-y-2 ${history ? 'max-h-72 overflow-y-auto' : ''}`}>
        {rows.map((row) =>
          history ? (
            <li key={`${row.kind}-${row.id}`} className="flex items-start justify-between gap-2 text-xs">
              <span className="min-w-0 text-slate-200">{historyLine(row)}</span>
              <span className="shrink-0 text-[10px] text-slate-500">{relativeTime(row.at)}</span>
            </li>
          ) : (
            <li key={`${row.kind}-${row.id}`} className="rounded-xl border border-sky-400/30 bg-sky-400/5 p-3 space-y-2" data-testid={`request-${row.kind}-${row.id}`}>
              <p className="text-xs text-slate-200">
                {row.kind === 'time' ? `Asks for ${(row.request as ScreenTimeRequest).minutes} more minutes` : requestLine(row.request as GuardRequest)}
                {row.request.note ? `: “${row.request.note}”` : ''}
              </p>
              <div className="flex flex-wrap gap-2">
                {row.kind === 'time' ? (
                  <>
                    <button type="button" className={yes} disabled={busy} onClick={() => decideTime.mutate({ id: row.id, grant: true })}>Grant</button>
                    <button type="button" className={no} disabled={busy} onClick={() => decideTime.mutate({ id: row.id, grant: false })}>Not today</button>
                  </>
                ) : (
                  <>
                    {row.kind === 'site' ? (
                      <>
                        <button type="button" className={yes} disabled={busy} onClick={() => decideGuard.mutate({ id: row.id, body: { approve: true, minutes: 60 } })}>Allow 1 hour</button>
                        <button type="button" className={yes} disabled={busy} onClick={() => decideGuard.mutate({ id: row.id, body: { approve: true, always: true } })}>Always allow</button>
                      </>
                    ) : (
                      <button type="button" className={yes} disabled={busy} onClick={() => decideGuard.mutate({ id: row.id, body: { approve: true } })}>
                        {row.kind === 'app' ? 'Approve app' : 'Allow'}
                      </button>
                    )}
                    <button type="button" className={no} disabled={busy} onClick={() => decideGuard.mutate({ id: row.id, body: { approve: false } })}>No</button>
                  </>
                )}
              </div>
            </li>
          ),
        )}
      </ul>
      {failure && <p className="text-xs text-amber-300">{failure}</p>}
    </MemberCard>
  );
}
