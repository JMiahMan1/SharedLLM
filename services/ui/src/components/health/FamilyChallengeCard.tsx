import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, Share2, Trophy } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { formatDayLabel, localDay } from '../../lib/healthHub';
import { errorText } from '../../lib/errorText';
import { challengeSharePreview, challengeShareText, standingLabel, type NameOf } from '../../lib/familyChallenge';
import { useFamilyDirectory } from '../chat/familyDirectory';
import ShareToChatSheet from '../bible/ShareToChatSheet';
import type { HealthChallenge, HealthChallengeKind } from '../../types/api';

const TARGETS = [1, 2, 3, 4, 5, 6, 7];

/**
 * The weekly family challenge (Health hub Phase 7) on Health → Today.
 *
 * An adult starts one for the week; anyone joins. Standings are the days each
 * member's own logs counted — water goal, sleep need, a recipe dinner or
 * their own daily step goal (steps come from geo, read by the gateway) — so a
 * day with nothing logged is simply not counted, and a member whose steps
 * could not be read says so instead of showing zero. No calories or weight,
 * so the card is the same for a child.
 */
export default function FamilyChallengeCard() {
  const queryClient = useQueryClient();
  const day = useMemo(() => localDay(), []);
  const lookup = useFamilyDirectory();
  const nameOf: NameOf = (userId) => lookup(userId)?.display_name || userId;
  const [kind, setKind] = useState<HealthChallengeKind | ''>('');
  const [target, setTarget] = useState(5);
  const [sharing, setSharing] = useState<HealthChallenge | null>(null);

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000 });
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['health-challenges', day],
    queryFn: () => api.getHealthChallenges(day),
    retry: false,
  });

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['health-challenges'] });

  const create = useMutation({
    mutationFn: (chosen: HealthChallengeKind) => api.createHealthChallenge({ kind: chosen, day, target_days: target }),
    onSuccess: (challenge) => {
      toast.success(`${challenge.title} started`);
      setKind('');
      refresh();
    },
    onError: (error) => toast.error(errorText(error, 'Could not start the challenge')),
  });
  const join = useMutation({
    mutationFn: ({ id, joined }: { id: number; joined: boolean }) =>
      joined ? api.leaveHealthChallenge(id) : api.joinHealthChallenge(id),
    onSuccess: refresh,
    onError: (error) => toast.error(errorText(error, 'Could not update the challenge')),
  });
  const end = useMutation({
    mutationFn: (id: number) => api.deleteHealthChallenge(id),
    onSuccess: refresh,
    onError: (error) => toast.error(errorText(error, 'Could not end the challenge')),
  });

  if (isLoading) return <div className="h-32 skeleton rounded-2xl" aria-hidden />;

  const open = data?.kinds.filter((option) => !option.taken) ?? [];
  const chosenKind = kind || open[0]?.kind || '';

  return (
    <section
      className="glass-panel rounded-2xl border border-white/5 p-4 sm:p-5"
      aria-labelledby="family-challenge-heading"
      data-testid="family-challenge"
    >
      <div className="flex items-center justify-between gap-3">
        <h3
          id="family-challenge-heading"
          className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
        >
          <Trophy size={13} className="text-amber-300" />
          Family challenge
        </h3>
        {data && (
          <span className="text-[10px] text-slate-500">
            {formatDayLabel(data.week_start)} – {formatDayLabel(data.week_end)}
          </span>
        )}
      </div>

      {isError || !data ? (
        <div className="mt-2 flex items-center gap-3" data-testid="family-challenge-error">
          <p className="text-xs text-amber-300">The family challenge could not be loaded.</p>
          <button type="button" onClick={() => void refetch()} className="glass-button min-h-11 px-3 text-xs">
            Try again
          </button>
        </div>
      ) : (
        <div className="mt-3 space-y-4">
          {data.challenges.length === 0 && (
            <p className="text-xs text-slate-400" data-testid="family-challenge-empty">
              No family challenge this week.{data.can_create ? ' Start one below.' : ' A parent can start one.'}
            </p>
          )}

          {data.challenges.map((challenge) => (
            <div key={challenge.id} className="space-y-2" data-testid={`challenge-${challenge.kind}`}>
              <div>
                <p className="text-sm font-semibold text-slate-100">
                  {challenge.title}: {challenge.target_days} {challenge.unit}
                </p>
                <p className="text-[11px] text-slate-500">{challenge.detail}</p>
              </div>
              {challenge.leaderboard.length === 0 ? (
                <p className="text-xs text-slate-400">Nobody has joined yet.</p>
              ) : (
                <ol className="space-y-1.5" aria-label={`${challenge.title} standings`}>
                  {challenge.leaderboard.map((row) => (
                    <li key={row.user_id} className="text-xs" data-testid={`standing-${challenge.kind}-${row.user_id}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="flex min-w-0 items-center gap-1 truncate text-slate-300">
                          {nameOf(row.user_id)}
                          {row.reached && <Check size={12} className="shrink-0 text-emerald-300" aria-label="reached" />}
                        </span>
                        <span className="shrink-0 tabular-nums text-slate-400">
                          {standingLabel(row, challenge.target_days, challenge.unit)}
                          {row.guideline_goal ? ' · guideline goal' : ''}
                        </span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                        <div
                          className="h-full rounded-full bg-amber-400"
                          style={{ width: `${Math.min(100, (row.count / challenge.target_days) * 100)}%` }}
                        />
                      </div>
                    </li>
                  ))}
                </ol>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => join.mutate({ id: challenge.id, joined: challenge.joined })}
                  disabled={join.isPending}
                  data-testid={`challenge-join-${challenge.kind}`}
                  className="glass-button min-h-11 px-3 text-xs disabled:opacity-60"
                >
                  {challenge.joined ? 'Leave' : 'Join'}
                </button>
                <button
                  type="button"
                  onClick={() => setSharing(challenge)}
                  data-testid={`challenge-share-${challenge.kind}`}
                  className="glass-button min-h-11 px-3 text-xs"
                >
                  <Share2 size={14} /> Share to chat
                </button>
                {me?.username && me.username.toLowerCase() === challenge.created_by && (
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`End the ${challenge.title} challenge for everyone?`)) end.mutate(challenge.id);
                    }}
                    disabled={end.isPending}
                    className="glass-button min-h-11 px-3 text-xs disabled:opacity-60"
                  >
                    End
                  </button>
                )}
              </div>
              {!challenge.joined && (
                <p className="text-[11px] text-slate-500">Joining shows the family how many {challenge.unit} counted for you.</p>
              )}
            </div>
          ))}

          {data.can_create && open.length > 0 && (
            <div className="space-y-2 border-t border-white/5 pt-3" data-testid="family-challenge-create">
              <p className="text-[11px] uppercase tracking-wider text-slate-500">Start a challenge this week</p>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={chosenKind}
                  onChange={(event) => setKind(event.target.value as HealthChallengeKind)}
                  aria-label="Challenge"
                  className="glass-input min-h-11 min-w-0 flex-1 px-2 text-base sm:text-sm"
                >
                  {open.map((option) => (
                    <option key={option.kind} value={option.kind}>
                      {option.title}
                    </option>
                  ))}
                </select>
                <select
                  value={target}
                  onChange={(event) => setTarget(Number(event.target.value))}
                  aria-label="Days to aim for"
                  className="glass-input min-h-11 w-24 px-2 text-base sm:text-sm"
                >
                  {TARGETS.map((value) => (
                    <option key={value} value={value}>
                      {value} of 7
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => chosenKind && create.mutate(chosenKind)}
                  disabled={!chosenKind || create.isPending}
                  data-testid="family-challenge-start"
                  className="glass-button min-h-11 px-3 text-xs disabled:opacity-60"
                >
                  {create.isPending ? <Loader2 size={14} className="animate-spin" /> : <Trophy size={14} />}
                  Start
                </button>
              </div>
              <p className="text-[11px] text-slate-500">
                {open.find((option) => option.kind === chosenKind)?.detail}
              </p>
            </div>
          )}
        </div>
      )}

      {sharing && (
        <ShareToChatSheet
          reference="Family challenge"
          text={challengeSharePreview(sharing)}
          heading="Share the family challenge to chat"
          previewIcon="🏆"
          buildMessage={(note) => challengeShareText(sharing, nameOf, note)}
          onClose={() => setSharing(null)}
        />
      )}
    </section>
  );
}
