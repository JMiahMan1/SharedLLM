import { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, BookOpenCheck, CalendarDays, Check, ChevronDown, ChevronRight, Loader2, Play, RotateCcw, Trophy, UserPlus, Users, X } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { BiblePlanEnrollment, BiblePlanGroupSummary, BiblePlanInfo } from '../../types/api';
import { errorText } from '../../lib/errorText';

/**
 * Reading plans: today's reading for each plan the reader follows, at the top
 * of the Today tab, and the catalogue to start a new one. Each reading opens in
 * the reader; marking a day read is one tap, and falling behind is a number
 * with a "catch me up" rather than a wall of red.
 */
export default function BiblePlans({ onOpenRef }: { onOpenRef: (ref: string) => void }) {
  const queryClient = useQueryClient();
  const { trigger } = useHaptics();
  const { user } = useAuth();
  const me = user?.username ?? '';
  const [browsing, setBrowsing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [groupsBusy, setGroupsBusy] = useState(false);
  const [togetherFor, setTogetherFor] = useState<BiblePlanEnrollment | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const { data, isLoading, error } = useQuery({ queryKey: ['bible-plans'], queryFn: () => api.getBiblePlans() });
  const { data: groupsData } = useQuery({ queryKey: ['bible-plan-groups'], queryFn: () => api.listBiblePlanGroups(), retry: false });

  const groupAction = async (work: () => Promise<unknown>, success?: (result: unknown) => string | null) => {
    setGroupsBusy(true);
    setMessage(null);
    try {
      const result = await work();
      const note = success ? success(result) : null;
      if (note) setMessage(note);
      await queryClient.invalidateQueries({ queryKey: ['bible-plan-groups'] });
      await queryClient.invalidateQueries({ queryKey: ['bible-plan-group'] });
      return result;
    } catch (e) {
      setMessage(errorText(e));
      return undefined;
    } finally {
      setGroupsBusy(false);
    }
  };

  const act = async (key: string, work: () => Promise<BiblePlanEnrollment | unknown>) => {
    setBusy(key);
    setMessage(null);
    try {
      const result = (await work()) as BiblePlanEnrollment | undefined;
      const badges = result?.newly_earned ?? [];
      if (badges.length) setMessage(`New badge: ${badges.map((b) => b.name).join(', ')}`);
      else if (result?.finished) setMessage(`You finished ${result.name}!`);
      await queryClient.invalidateQueries({ queryKey: ['bible-plans'] });
      void queryClient.invalidateQueries({ queryKey: ['bible-plan'] });
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const mine = data?.mine ?? [];
  const following = new Set(mine.map((m) => m.plan_id));
  const groups = groupsData?.groups ?? [];
  const invites = groups.filter((g) => g.status === 'invited');
  const joinedGroups = groups.filter((g) => g.status === 'joined');

  return (
    <section className="space-y-3" data-testid="bible-plans">
      <div className="flex items-center gap-2 px-1">
        <CalendarDays size={16} className="text-sky-300" />
        <h2 className="text-sm font-bold text-white">Reading plans</h2>
        <button
          type="button"
          onClick={() => setBrowsing(!browsing)}
          aria-expanded={browsing}
          data-testid="bible-plans-browse"
          className="ml-auto min-h-11 px-3 rounded-xl text-xs text-sky-200 hover:bg-white/5 flex items-center gap-1"
        >
          {browsing ? 'Close' : mine.length ? 'Find another plan' : 'Choose a plan'}
          {browsing ? <X size={13} /> : <ChevronRight size={13} />}
        </button>
      </div>

      {error && <p className="glass-panel rounded-2xl p-4 text-sm text-amber-300/90" role="alert">{errorText(error)}</p>}
      {message && (
        <p className="rounded-xl bg-sky-500/10 border border-sky-400/20 px-3 py-2 text-sm text-sky-100" role="status">
          {message}
        </p>
      )}
      {isLoading && <div className="glass-panel rounded-2xl p-4"><div className="skeleton h-4 w-1/2 rounded" /></div>}

      {invites.map((g) => (
        <div
          key={g.id}
          className="glass-panel rounded-2xl p-4 border border-violet-400/30"
          data-testid={`bible-group-invite-${g.id}`}
        >
          <div className="flex items-start gap-3">
            <Users size={18} className="mt-0.5 text-violet-300" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-white">{g.created_by} invited you to read together</p>
              <p className="text-xs text-slate-400">
                {g.name ? `“${g.name}” · ` : ''}
                {g.plan_name}
              </p>
            </div>
          </div>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={groupsBusy}
              onClick={() => void groupAction(() => api.joinBiblePlanGroup(g.id))}
              data-testid={`bible-group-join-${g.id}`}
              className="min-h-11 rounded-xl bg-violet-500 px-4 text-sm font-semibold text-white disabled:opacity-60"
            >
              Join
            </button>
            <button
              type="button"
              disabled={groupsBusy}
              onClick={() => void groupAction(() => api.declineBiblePlanGroup(g.id))}
              data-testid={`bible-group-decline-${g.id}`}
              className="min-h-11 rounded-xl px-4 text-sm text-slate-400 hover:bg-white/5 disabled:opacity-60"
            >
              Decline
            </button>
          </div>
        </div>
      ))}

      {joinedGroups.map((g) => (
        <GroupPanel key={g.id} group={g} me={me} busy={groupsBusy} onAction={groupAction} />
      ))}

      {!isLoading && !mine.length && !browsing && (
        <p className="glass-panel rounded-2xl p-4 text-sm text-slate-400">
          A plan gives each day a reading, from a chapter of Proverbs to the whole Bible in a year.
        </p>
      )}

      {mine.map((plan) => (
        <PlanCard key={plan.plan_id} plan={plan} busy={busy} onOpenRef={onOpenRef} act={act} trigger={trigger} onTogether={setTogetherFor} />
      ))}

      {togetherFor && (
        <ReadTogetherDialog
          plan={togetherFor}
          onClose={() => setTogetherFor(null)}
          onCreated={(note) => {
            setTogetherFor(null);
            setMessage(note);
            void queryClient.invalidateQueries({ queryKey: ['bible-plan-groups'] });
            void queryClient.invalidateQueries({ queryKey: ['bible-plans'] });
          }}
        />
      )}

      {browsing && data && (
        <Catalogue
          plans={data.catalogue}
          following={following}
          busy={busy}
          onStart={(id) =>
            void act(`start-${id}`, async () => {
              const r = await api.startBiblePlan(id);
              void trigger('success');
              setBrowsing(false);
              return r;
            })
          }
        />
      )}
    </section>
  );
}

function PlanCard({
  plan,
  busy,
  onOpenRef,
  act,
  trigger,
  onTogether,
}: {
  plan: BiblePlanEnrollment;
  busy: string | null;
  onOpenRef: (ref: string) => void;
  act: (key: string, work: () => Promise<unknown>) => Promise<void>;
  trigger: (kind: 'light' | 'success') => Promise<void> | void;
  onTogether: (plan: BiblePlanEnrollment) => void;
}) {
  const [showDays, setShowDays] = useState(false);
  // Stopping cannot be undone (starting again starts at day 1), so it asks once.
  const [confirmStop, setConfirmStop] = useState(false);
  const today = plan.today;
  const doneToday = today ? plan.completed_days.includes(today.day) : false;

  return (
    <div className="glass-panel rounded-2xl p-4 border border-sky-400/20" data-testid={`bible-plan-${plan.plan_id}`}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-white">{plan.name}</p>
          <p className="text-xs text-slate-400">
            {plan.finished ? 'Finished' : `Day ${today?.day ?? plan.calendar_day} of ${plan.days}`} · {plan.completed} read
          </p>
        </div>
        <span className="text-sm font-bold text-sky-200 tabular-nums">{plan.percent}%</span>
      </div>
      <div className="mt-2 h-2 rounded-full bg-white/10 overflow-hidden">
        <div className="h-full bg-sky-400 transition-all" style={{ width: `${plan.percent}%` }} />
      </div>

      {plan.finished ? (
        <p className="mt-3 flex items-center gap-2 text-sm text-emerald-300">
          <Trophy size={14} /> Every day read{plan.finished_on ? `, finished ${plan.finished_on}` : ''}.
        </p>
      ) : today ? (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap gap-2">
            {today.readings.map((r) => (
              <button
                key={r.ref}
                type="button"
                onClick={() => onOpenRef(r.ref)}
                className="min-h-11 rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-slate-100 hover:border-sky-400/50 flex items-center gap-1.5"
              >
                <Play size={12} className="text-sky-300" /> {r.ref}
              </button>
            ))}
          </div>
          <button
            type="button"
            disabled={busy !== null || doneToday}
            onClick={() =>
              void act(`day-${plan.plan_id}`, async () => {
                const r = await api.completeBiblePlanDay(plan.plan_id, today.day);
                void trigger('success');
                return r;
              })
            }
            data-testid={`bible-plan-complete-${plan.plan_id}`}
            className="min-h-11 w-full rounded-xl bg-sky-500 text-white text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-60"
          >
            {busy === `day-${plan.plan_id}` ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            {doneToday ? `Day ${today.day} read` : `Mark day ${today.day} read`}
          </button>
          {doneToday && <PlanReflection planId={plan.plan_id} day={today.day} />}
        </div>
      ) : null}

      {plan.behind > 0 && !plan.finished && (
        <div className="mt-2 flex items-center gap-2 text-xs text-amber-200">
          <span className="flex-1">
            {plan.behind} day{plan.behind === 1 ? '' : 's'} behind.
          </span>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void act(`catch-${plan.plan_id}`, () => api.catchUpBiblePlan(plan.plan_id))}
            className="min-h-11 rounded-xl px-3 bg-amber-500/15 text-amber-100 flex items-center gap-1.5"
            data-testid={`bible-plan-catchup-${plan.plan_id}`}
          >
            <RotateCcw size={12} /> Catch me up
          </button>
        </div>
      )}

      <ReadingAlong planId={plan.plan_id} />

      <button
        type="button"
        onClick={() => setShowDays(!showDays)}
        aria-expanded={showDays}
        className="mt-2 min-h-11 text-xs text-slate-400 flex items-center gap-1"
      >
        <ChevronDown size={13} className={showDays ? 'rotate-180 transition-transform' : 'transition-transform'} /> All days
      </button>
      {showDays && <PlanDays planId={plan.plan_id} onOpenRef={onOpenRef} act={act} />}

      <div className="mt-1 flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => onTogether(plan)}
          data-testid={`bible-plan-together-${plan.plan_id}`}
          className="min-h-11 px-3 rounded-xl text-xs text-violet-300 hover:bg-white/5 flex items-center gap-1.5 mr-auto"
        >
          <UserPlus size={12} /> Read together
        </button>
        {confirmStop ? (
          <>
            <span className="text-[11px] text-slate-400">Stop {plan.name}? Starting it again begins at day 1.</span>
            <button
              type="button"
              onClick={() => setConfirmStop(false)}
              className="min-h-11 px-3 rounded-xl text-xs text-slate-300 hover:bg-white/5"
            >
              Keep it
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void act(`stop-${plan.plan_id}`, () => api.stopBiblePlan(plan.plan_id))}
              data-testid={`bible-plan-stop-confirm-${plan.plan_id}`}
              className="min-h-11 px-3 rounded-xl bg-rose-500/15 text-xs text-rose-200"
            >
              Stop plan
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => setConfirmStop(true)}
            data-testid={`bible-plan-stop-${plan.plan_id}`}
            className="min-h-11 px-2 text-[11px] text-slate-500 hover:text-rose-300"
          >
            Stop this plan
          </button>
        )}
      </div>
    </div>
  );
}

/** Family members on the same plan who share their reading with you. */
function ReadingAlong({ planId }: { planId: string }) {
  const { data } = useQuery({
    queryKey: ['bible-plan-family', planId],
    queryFn: () => api.getBiblePlanFamily(planId),
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
  if (!data?.members.length) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-400" data-testid={`bible-plan-family-${planId}`}>
      <Users size={13} className="text-sky-300" />
      <span>Reading along:</span>
      {data.members.map((m) => (
        <span key={m.username} className="rounded-full bg-white/5 px-2 py-0.5 text-slate-200 capitalize">
          {m.username} {m.finished ? '✓' : `${m.percent}%`}
        </span>
      ))}
    </div>
  );
}

function PlanDays({
  planId,
  onOpenRef,
  act,
}: {
  planId: string;
  onOpenRef: (ref: string) => void;
  act: (key: string, work: () => Promise<unknown>) => Promise<void>;
}) {
  const { data, isLoading } = useQuery({ queryKey: ['bible-plan', planId], queryFn: () => api.getBiblePlan(planId) });
  if (isLoading || !data) return <Loader2 size={14} className="animate-spin text-slate-500" />;
  return (
    <ol className="max-h-80 overflow-y-auto space-y-1 pr-1 custom-scrollbar" data-testid="bible-plan-days">
      {data.days.map((d) => (
        <li key={d.day} className="flex items-start gap-2 rounded-lg px-2 py-1 hover:bg-white/[0.03]">
          <button
            type="button"
            aria-label={d.done ? `Mark day ${d.day} unread` : `Mark day ${d.day} read`}
            aria-pressed={d.done}
            onClick={() =>
              void act(`day-${planId}-${d.day}`, () =>
                d.done ? api.undoBiblePlanDay(planId, d.day) : api.completeBiblePlanDay(planId, d.day),
              )
            }
            className={`min-h-11 min-w-11 shrink-0 rounded-lg border flex items-center justify-center ${d.done ? 'bg-sky-500 border-sky-400 text-white' : 'border-white/15 text-transparent'}`}
          >
            <Check size={13} />
          </button>
          {/* The day sits above its readings so a long book name ("1 Thessalonians")
              keeps its own line on a phone instead of being cut off. */}
          <span className="min-w-0 flex-1">
            <span className="block pt-0.5 text-xs text-slate-500 tabular-nums">Day {d.day}</span>
            <span className="flex flex-wrap gap-x-1">
              {d.readings.map((r) => (
                <button key={r.ref} type="button" onClick={() => onOpenRef(r.ref)} className="min-h-11 px-1.5 text-left text-sm text-slate-200 hover:text-sky-200">
                  {r.ref}
                </button>
              ))}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}

function Catalogue({
  plans,
  following,
  busy,
  onStart,
}: {
  plans: BiblePlanInfo[];
  following: Set<string>;
  busy: string | null;
  onStart: (id: string) => void;
}) {
  const categories = [...new Set(plans.map((p) => p.category))];
  return (
    <div className="space-y-4" data-testid="bible-plan-catalogue">
      {categories.map((category) => (
        <div key={category} className="space-y-2">
          <h3 className="px-1 text-xs font-semibold uppercase tracking-wider text-slate-500">{category}</h3>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {plans
              .filter((p) => p.category === category)
              .map((p) => (
                <div key={p.id} className="glass-panel rounded-2xl p-4 border border-white/10">
                  <p className="font-semibold text-white">{p.name}</p>
                  <p className="mt-1 text-sm text-slate-400 leading-relaxed">{p.blurb}</p>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <span className="text-xs text-slate-500">
                      {p.days} days · {p.chapters} chapters
                    </span>
                    <button
                      type="button"
                      disabled={busy !== null || following.has(p.id)}
                      onClick={() => onStart(p.id)}
                      data-testid={`bible-plan-start-${p.id}`}
                      className="min-h-11 rounded-xl bg-sky-500 px-3 text-sm font-semibold text-white flex items-center gap-1.5 disabled:opacity-50"
                    >
                      {busy === `start-${p.id}` ? <Loader2 size={14} className="animate-spin" /> : <BookOpenCheck size={14} />}
                      {following.has(p.id) ? 'Following' : 'Start'}
                    </button>
                  </div>
                </div>
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * What the reader took from today's reading, in their own words. Kept with
 * the plan and put into study memory, so Jarvis can recall it later.
 */
function PlanReflection({ planId, day }: { planId: string; day: number }) {
  const queryClient = useQueryClient();
  const key = ['bible-plan-reflections', planId];
  const { data } = useQuery({ queryKey: key, queryFn: () => api.getBiblePlanReflections(planId), retry: false });
  const saved = data?.reflections.find((r) => r.day === day)?.text ?? '';
  const [text, setText] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => api.saveBiblePlanReflection(planId, day, (text ?? saved).trim()),
    onSuccess: () => {
      setText(null);
      void queryClient.invalidateQueries({ queryKey: key });
    },
  });
  const value = text ?? saved;
  return (
    <div className="space-y-1.5" data-testid={`bible-plan-reflection-${planId}`}>
      <label className="block text-[11px] text-slate-400">
        What stood out to you today?
        <textarea
          rows={2}
          value={value}
          onChange={(e) => setText(e.target.value)}
          className="mt-1 w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-100"
        />
      </label>
      {text !== null && text.trim() !== saved && (
        <button
          type="button"
          disabled={!text.trim() || save.isPending}
          onClick={() => save.mutate()}
          className="min-h-11 rounded-xl border border-white/10 px-3 text-xs text-sky-200 disabled:opacity-50"
        >
          Save reflection
        </button>
      )}
      {save.isSuccess && save.data.remembered === false && (
        <p className="text-[11px] text-amber-300">Saved. Study memory was not reachable, so Jarvis will not recall it yet.</p>
      )}
      {save.isError && <p className="text-[11px] text-amber-300">{errorText(save.error, 'The reflection did not save.')}</p>}
    </div>
  );
}

/** One reading group the reader belongs to: who is in it, how far along, and a nudge. */
function GroupPanel({
  group,
  me,
  busy,
  onAction,
}: {
  group: BiblePlanGroupSummary;
  me: string;
  busy: boolean;
  onAction: (work: () => Promise<unknown>, success?: (result: unknown) => string | null) => Promise<unknown>;
}) {
  const { data } = useQuery({
    queryKey: ['bible-plan-group', group.id],
    queryFn: () => api.getBiblePlanGroup(group.id),
    retry: false,
  });
  if (!data) {
    return <div className="glass-panel rounded-2xl p-4 text-xs text-slate-500">Loading the group…</div>;
  }
  const label = data.name ? `“${data.name}”` : data.plan_name;
  return (
    <div className="glass-panel rounded-2xl p-4 border border-violet-400/20" data-testid={`bible-group-${group.id}`}>
      <div className="flex items-center gap-2">
        <Users size={16} className="text-violet-300" />
        <p className="min-w-0 flex-1 font-semibold text-white">{label}</p>
        <span className="text-[11px] text-slate-500">started {data.started_on}</span>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        {data.plan_name} · {data.counts.read_today} of {data.counts.joined} read today · {data.counts.finished} finished
      </p>
      <ul className="mt-2 space-y-1.5">
        {data.members.map((m) => (
          <li key={m.username} className="flex items-center gap-2 text-sm" data-testid={`bible-group-member-${m.username}`}>
            <span className="min-w-0 flex-1 capitalize text-slate-100">{m.username}</span>
            {m.status === 'declined' ? (
              <span className="text-[11px] text-slate-500">declined</span>
            ) : m.status === 'invited' ? (
              <span className="text-[11px] text-amber-300">invited</span>
            ) : (
              <>
                <span className="text-xs tabular-nums text-slate-400">{m.percent}%</span>
                {m.read_today ? (
                  <span className="flex items-center gap-1 text-xs text-emerald-400">
                    <Check size={12} /> read today
                  </span>
                ) : (
                  <span className="text-xs text-slate-600">·</span>
                )}
                {!m.finished && m.username !== me && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void onAction(
                        () => api.nudgeBiblePlanGroup(group.id, m.username),
                        (result) => {
                          const r = result as { skipped: { username: string; reason: string }[] };
                          return r.skipped?.length ? `${m.username}: ${r.skipped[0].reason}` : `Nudged ${m.username}`;
                        },
                      )
                    }
                    data-testid={`bible-group-nudge-${m.username}`}
                    className="min-h-11 rounded-lg px-2 text-[11px] text-violet-300 hover:bg-white/5 disabled:opacity-60"
                  >
                    Nudge
                  </button>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
      <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void onAction(
              () => api.nudgeBiblePlanGroup(group.id),
              (result) => {
                const r = result as { nudged: string[] };
                const n = r.nudged?.length ?? 0;
                return n ? `Nudged ${n} ${n === 1 ? 'reader' : 'readers'}` : 'Everyone has read today';
              },
            )
          }
          data-testid={`bible-group-nudge-all-${group.id}`}
          className="min-h-11 px-3 rounded-xl text-xs text-violet-200 hover:bg-white/5 flex items-center gap-1.5 mr-auto"
        >
          <Bell size={12} /> Remind the group
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void onAction(() => api.leaveBiblePlanGroup(group.id))}
          data-testid={`bible-group-leave-${group.id}`}
          className="min-h-11 px-3 text-[11px] text-slate-500 hover:bg-white/5 hover:text-rose-300 disabled:opacity-60"
        >
          Leave
        </button>
        {data.is_creator && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void onAction(() => api.deleteBiblePlanGroup(group.id))}
            data-testid={`bible-group-delete-${group.id}`}
            className="min-h-11 px-3 text-[11px] text-slate-500 hover:bg-white/5 hover:text-rose-300 disabled:opacity-60"
          >
            End group
          </button>
        )}
      </div>
    </div>
  );
}

/** Create a group for a plan the reader follows and invite people to do it together. */
function ReadTogetherDialog({
  plan,
  onClose,
  onCreated,
}: {
  plan: BiblePlanEnrollment;
  onClose: () => void;
  onCreated: (note: string) => void;
}) {
  const { user } = useAuth();
  const me = user?.username ?? '';
  const [name, setName] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: directory } = useQuery({
    queryKey: ['family-directory'],
    queryFn: () => api.getFamilyDirectory(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const people = (directory ?? []).filter((p) => p.username && p.username !== me);
  const toggle = (username: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(username)) next.delete(username);
      else next.add(username);
      return next;
    });
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.createBiblePlanGroup(plan.plan_id, {
        name: name.trim(),
        members: [...selected],
        started_on: new Date().toLocaleDateString('en-CA'),
      });
      const invited = result.invited?.length ?? 0;
      onCreated(`Invited ${invited} ${invited === 1 ? 'person' : 'people'} to read ${plan.name} together`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/60 sm:items-center" role="dialog" aria-modal="true" data-testid="bible-group-dialog">
      <div className="w-full max-w-md glass-panel rounded-t-2xl sm:rounded-2xl p-5 border border-violet-400/30">
        <div className="flex items-center gap-2">
          <Users size={16} className="text-violet-300" />
          <h3 className="min-w-0 flex-1 font-semibold text-white">Read together</h3>
          <button type="button" onClick={onClose} aria-label="Close" data-testid="bible-group-dialog-close" className="min-h-11 px-2 text-slate-400 hover:text-white">
            <X size={18} />
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-400">{plan.name}</p>
        <label className="mt-3 block text-[11px] text-slate-400">
          Group name (optional)
          <input
            type="text"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. NT before Christmas"
            data-testid="bible-group-name"
            className="mt-1 w-full min-h-11 rounded-xl border border-white/10 bg-white/5 px-3 text-sm text-slate-100 placeholder:text-slate-600"
          />
        </label>
        <fieldset className="mt-3">
          <legend className="text-[11px] text-slate-400">Who reads with you?</legend>
          <div className="mt-1 max-h-56 space-y-1 overflow-y-auto">
            {people.map((p) => (
              <label key={p.username} className="flex cursor-pointer items-center gap-3 rounded-xl px-2 py-2 hover:bg-white/5">
                <input
                  type="checkbox"
                  checked={selected.has(p.username)}
                  onChange={() => toggle(p.username)}
                  data-testid={`bible-group-pick-${p.username}`}
                  className="h-5 w-5"
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm text-slate-100">{p.display_name || p.username}</span>
                  <span className="block text-[11px] text-slate-500">{p.username}</span>
                </span>
              </label>
            ))}
            {!people.length && <p className="px-2 py-3 text-xs text-slate-500">No one else in the household yet.</p>}
          </div>
        </fieldset>
        {error && <p className="mt-2 text-xs text-rose-300" role="alert">{error}</p>}
        <button
          type="button"
          disabled={busy || !selected.size}
          onClick={() => void submit()}
          data-testid="bible-group-create"
          className="mt-3 min-h-11 w-full rounded-xl bg-violet-500 text-sm font-semibold text-white flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />}
          Invite {selected.size || ''} {selected.size === 1 ? 'person' : 'people'} to read together
        </button>
      </div>
    </div>
  );
}
