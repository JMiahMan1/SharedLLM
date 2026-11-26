import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { History, Inbox } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { requestLine } from '../../lib/guardRequests';
import { GRANT_MAX, GRANT_MIN, QUICK_PICKS, grantMinutes } from '../../lib/timeGrant';
import { relativeTime } from '../../lib/wanderTrips';
import MemberCard from '../member/MemberCard';
import { guardRequestsQuery, timeRequestsQuery } from '../../lib/memberPage';
import { LEVEL_STYLES, LEVEL_WORDS, factChips, profileLines, ratingLine } from '../../lib/appVetting';
import type { GuardAppProfile, GuardRequest, RequestVetting, ScreenTimeRequest } from '../../types/api';

type Kind = GuardRequest['kind'] | 'time';
type Row =
  | { kind: 'time'; id: number; at: string; request: ScreenTimeRequest }
  | { kind: GuardRequest['kind']; id: number; at: string; request: GuardRequest };

const ALL_KINDS: Kind[] = ['time', 'app', 'app_unblock', 'permission', 'site'];

function historyLine(row: Row): string {
  if (row.kind === 'time') {
    const r = row.request as ScreenTimeRequest;
    return r.status === 'granted' ? `Asked for ${r.minutes} more minutes: given ${r.granted_minutes}` : `Asked for ${r.minutes} more minutes: not that day`;
  }
  const r = row.request as GuardRequest;
  return `${requestLine(r).replace(' - waiting for your OK', '')}: ${r.status === 'approved' ? 'yes' : 'no'}${r.decided_by ? ` (${r.decided_by})` : ''}`;
}

/** What the phone said about the app: version, where it came from, size, what
 *  it can reach, who signed it. Nothing is shown that the phone did not send. */
function AppProfile({ id, profile }: { id: number; profile?: GuardAppProfile }) {
  const lines = profileLines(profile);
  if (lines.length === 0) return null;
  return (
    <ul className="space-y-0.5 rounded-lg border border-white/10 bg-white/[0.02] p-2.5 text-xs text-slate-300" data-testid={`app-profile-${id}`}>
      {lines.map((line) => (
        <li key={line} className="break-words">{line}</li>
      ))}
    </ul>
  );
}

/** App vetting's verdict on an app request: the rating against the limit, the
 *  review's concerns and the listing's facts, or that it is still being checked. */
function RequestVerdict({ id, vetting }: { id: number; vetting: RequestVetting }) {
  const found = vetting.lookup === 'found';
  const rating = ratingLine(vetting);
  const facts = factChips({ rating: null, ads: vetting.ads ?? null, in_app_purchases: vetting.in_app_purchases ?? null,
    interactive: vetting.interactive || [], store: null });
  const checking = vetting.lookup === 'checking' || vetting.lookup === 'pending' || (found && vetting.review_state === 'checking');
  return (
    <div className="min-w-0 space-y-1.5 rounded-lg border border-white/10 bg-slate-950/30 p-2.5 text-xs" data-testid={`request-verdict-${id}`}>
      {rating && (
        <p className={`break-words font-semibold ${vetting.over_age ? 'text-amber-200' : 'text-slate-200'}`}>{rating}</p>
      )}
      {checking ? (
        <p className="text-slate-400" role="status">Checking its Play listing and reviews…</p>
      ) : !found ? (
        <p className="break-words text-slate-300">{vetting.line}</p>
      ) : null}
      {(vetting.security || []).length > 0 && (
        <ul className="space-y-1" aria-label="Security">
          {(vetting.security || []).map((f) => (
            <li key={`${f.check}-${f.text}`} className="flex min-w-0 flex-wrap items-center gap-1.5">
              <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${LEVEL_STYLES[f.severity]}`}>{LEVEL_WORDS[f.severity]}</span>
              <span className="min-w-0 break-words text-slate-200">{f.text}</span>
            </li>
          ))}
        </ul>
      )}
      {(vetting.concerns || []).length > 0 && (
        <ul className="space-y-1">
          {(vetting.concerns || []).map((c) => (
            <li key={c.label} className="flex min-w-0 flex-wrap items-center gap-1.5">
              <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${LEVEL_STYLES[c.severity]}`}>{LEVEL_WORDS[c.severity]}</span>
              <span className="min-w-0 break-words text-slate-200">{c.label}</span>
            </li>
          ))}
        </ul>
      )}
      {found && !checking && (vetting.concerns || []).length === 0 && vetting.review_state === 'ready' && (
        <p className="text-emerald-200/90">The review found nothing to worry about.</p>
      )}
      {facts.length > 0 && <p className="break-words text-slate-400">{facts.join(' · ')}</p>}
      {vetting.summary && <p className="break-words leading-relaxed text-slate-300">{vetting.summary}</p>}
    </div>
  );
}

/**
 * A request for more time: the asked minutes in a field the parent can change
 * (less or more than asked, 5 to 240, with quick picks), then Approve gives
 * exactly that, or Not today.
 */
function TimeRequestAnswer({ request, busy, onAnswer, yes, no }: {
  request: ScreenTimeRequest;
  busy: boolean;
  onAnswer: (grant: boolean, minutes?: number) => void;
  yes: string;
  no: string;
}) {
  const [text, setText] = useState(String(request.minutes));
  const minutes = grantMinutes(text);
  const picks = Array.from(new Set([...QUICK_PICKS, request.minutes])).sort((a, b) => a - b);
  const pick = 'min-h-11 min-w-11 rounded-xl border px-2.5 text-xs font-medium transition-colors';
  const fieldId = `grant-minutes-${request.id}`;
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={fieldId} className="text-xs text-slate-300">Give</label>
        <input
          id={fieldId}
          type="number"
          inputMode="numeric"
          min={GRANT_MIN}
          max={GRANT_MAX}
          step={5}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-invalid={minutes === null}
          aria-describedby={`${fieldId}-hint`}
          data-testid={`grant-minutes-${request.id}`}
          className="min-h-11 w-20 rounded-xl border border-white/15 bg-slate-950/40 px-2.5 text-sm text-white"
        />
        <span className="text-xs text-slate-300">minutes</span>
      </div>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Quick picks">
        {picks.map((m) => (
          <button
            key={m}
            type="button"
            disabled={busy}
            aria-pressed={minutes === m}
            onClick={() => setText(String(m))}
            className={`${pick} ${minutes === m ? 'border-sky-400/50 bg-sky-500/20 text-sky-100' : 'border-white/10 bg-white/5 text-slate-300 hover:bg-white/10'}`}
          >
            {m}
          </button>
        ))}
      </div>
      <p id={`${fieldId}-hint`} className={`text-[11px] ${minutes === null ? 'text-amber-300' : 'text-slate-400'}`}>
        {minutes === null ? `Enter ${GRANT_MIN} to ${GRANT_MAX} minutes.` : minutes === request.minutes ? 'What they asked for.' : `They asked for ${request.minutes}.`}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={yes} disabled={busy || minutes === null} onClick={() => minutes !== null && onAnswer(true, minutes)}>
          {minutes === null ? 'Approve' : `Approve ${minutes} min`}
        </button>
        <button type="button" className={no} disabled={busy} onClick={() => onAnswer(false)}>Not today</button>
      </div>
    </div>
  );
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
  const decideTime = useMutation({
    mutationFn: ({ id, grant, minutes }: { id: number; grant: boolean; minutes?: number }) => api.decideScreenTimeRequest(id, grant, minutes),
    onSuccess: refresh,
  });

  const rows: Row[] = [
    ...(wantsTime ? (time.data || []).filter((r) => r.username === username).map((r) => ({ kind: 'time' as const, id: r.id, at: r.created_at, request: r })) : []),
    ...(wantsGuard ? (guard.data || []).filter((r) => r.username === username && kinds.includes(r.kind)).map((r) => ({ kind: r.kind, id: r.id, at: r.created_at, request: r })) : []),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const failed = [decideGuard.error, decideTime.error].find(Boolean);
  const failure = isAxiosError(failed) ? (failed.response?.data as { detail?: string })?.detail || 'That did not save.' : failed ? 'That did not save.' : null;
  const busy = decideGuard.isPending || decideTime.isPending;
  const yes = 'glass-button min-h-11 rounded-xl px-3.5 text-xs font-semibold text-sky-100 border-sky-400/30 bg-sky-500/15 hover:bg-sky-500/25';
  const no = 'min-h-11 rounded-xl border border-white/10 bg-white/5 px-3.5 text-xs font-medium text-slate-300 hover:bg-white/10 hover:text-white transition-colors';

  return (
    <MemberCard
      title={history ? 'Request history' : `${name} is asking`}
      icon={history ? History : Inbox}
      tone={history ? 'text-slate-300' : 'text-sky-300'}
      testId={history ? 'request-history' : 'open-requests'}
      aside={!history && rows.length > 0 ? `${rows.length} waiting` : undefined}
      isLoading={(wantsTime && time.isLoading) || (wantsGuard && guard.isLoading)}
      error={time.isError || guard.isError ? `${name}'s requests could not be loaded.` : null}
      empty={rows.length === 0 ? (history ? 'No answered requests yet.' : 'Nothing waiting for an answer.') : null}
    >
      <ul className={`space-y-2.5 ${history ? 'max-h-72 overflow-y-auto pr-1' : ''}`}>
        {rows.map((row) =>
          history ? (
            <li key={`${row.kind}-${row.id}`} className="flex items-start justify-between gap-3 rounded-xl border border-white/5 bg-white/[0.03] px-3 py-2.5 text-xs">
              <span className="min-w-0 leading-relaxed text-slate-200">{historyLine(row)}</span>
              <span className="shrink-0 rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-slate-400">{relativeTime(row.at)}</span>
            </li>
          ) : (
            <li key={`${row.kind}-${row.id}`} className="rounded-xl border border-sky-400/30 bg-gradient-to-br from-sky-500/10 to-sky-400/[0.03] p-3.5 space-y-3" data-testid={`request-${row.kind}-${row.id}`} data-focus-id={`${row.kind === 'time' ? 'time' : 'guard'}-${row.id}`}>
              <div className="flex items-start justify-between gap-2">
                <p className="text-xs font-medium leading-relaxed text-slate-100">
                  {row.kind === 'time' ? `Asks for ${(row.request as ScreenTimeRequest).minutes} more minutes` : requestLine(row.request as GuardRequest)}
                  {row.request.note ? `: “${row.request.note}”` : ''}
                </p>
                <span className="shrink-0 rounded-full border border-sky-400/20 bg-sky-400/10 px-2 py-0.5 text-[10px] font-medium text-sky-200">
                  {relativeTime(row.at)}
                </span>
              </div>
              {row.kind !== 'time' && <AppProfile id={row.id} profile={(row.request as GuardRequest).meta} />}
              {row.kind !== 'time' && (row.request as GuardRequest).vetting && (
                <RequestVerdict id={row.id} vetting={(row.request as GuardRequest).vetting as RequestVetting} />
              )}
              {row.kind === 'time' ? (
                <TimeRequestAnswer
                  request={row.request as ScreenTimeRequest}
                  busy={busy}
                  yes={yes}
                  no={no}
                  onAnswer={(grant, minutes) => decideTime.mutate({ id: row.id, grant, ...(grant ? { minutes } : {}) })}
                />
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  {row.kind === 'site' || row.kind === 'app_unblock' ? (
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
                </div>
              )}
            </li>
          ),
        )}
      </ul>
      {failure && <p className="text-xs text-amber-300">{failure}</p>}
    </MemberCard>
  );
}
