import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpenCheck, CalendarDays, Check, ChevronDown, ChevronRight, Loader2, Play, RotateCcw, Trophy, X } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { BiblePlanEnrollment, BiblePlanInfo } from '../../types/api';

function errorText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  return e instanceof Error ? e.message : 'Something went wrong';
}

/**
 * Reading plans: today's reading for each plan the reader follows, at the top
 * of the Today tab, and the catalogue to start a new one. Each reading opens in
 * the reader; marking a day read is one tap, and falling behind is a number
 * with a "catch me up" rather than a wall of red.
 */
export default function BiblePlans({ onOpenRef }: { onOpenRef: (ref: string) => void }) {
  const queryClient = useQueryClient();
  const { trigger } = useHaptics();
  const [browsing, setBrowsing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const { data, isLoading, error } = useQuery({ queryKey: ['bible-plans'], queryFn: () => api.getBiblePlans() });

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

      {!isLoading && !mine.length && !browsing && (
        <p className="glass-panel rounded-2xl p-4 text-sm text-slate-400">
          A plan gives each day a reading, from a chapter of Proverbs to the whole Bible in a year.
        </p>
      )}

      {mine.map((plan) => (
        <PlanCard key={plan.plan_id} plan={plan} busy={busy} onOpenRef={onOpenRef} act={act} trigger={trigger} />
      ))}

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
}: {
  plan: BiblePlanEnrollment;
  busy: string | null;
  onOpenRef: (ref: string) => void;
  act: (key: string, work: () => Promise<unknown>) => Promise<void>;
  trigger: (kind: 'light' | 'success') => Promise<void> | void;
}) {
  const [showDays, setShowDays] = useState(false);
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

      <button
        type="button"
        onClick={() => setShowDays(!showDays)}
        aria-expanded={showDays}
        className="mt-2 min-h-11 text-xs text-slate-400 flex items-center gap-1"
      >
        <ChevronDown size={13} className={showDays ? 'rotate-180 transition-transform' : 'transition-transform'} /> All days
      </button>
      {showDays && <PlanDays planId={plan.plan_id} onOpenRef={onOpenRef} act={act} />}

      <div className="mt-1 flex justify-end">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void act(`stop-${plan.plan_id}`, () => api.stopBiblePlan(plan.plan_id))}
          className="min-h-11 px-2 text-[11px] text-slate-500 hover:text-rose-300"
        >
          Stop this plan
        </button>
      </div>
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
        <li key={d.day} className="flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-white/[0.03]">
          <button
            type="button"
            aria-label={d.done ? `Mark day ${d.day} unread` : `Mark day ${d.day} read`}
            aria-pressed={d.done}
            onClick={() =>
              void act(`day-${planId}-${d.day}`, () =>
                d.done ? api.undoBiblePlanDay(planId, d.day) : api.completeBiblePlanDay(planId, d.day),
              )
            }
            className={`min-h-9 min-w-9 rounded-lg border flex items-center justify-center ${d.done ? 'bg-sky-500 border-sky-400 text-white' : 'border-white/15 text-transparent'}`}
          >
            <Check size={13} />
          </button>
          <span className="w-12 text-xs text-slate-500 tabular-nums">Day {d.day}</span>
          <span className="flex flex-wrap gap-x-2">
            {d.readings.map((r) => (
              <button key={r.ref} type="button" onClick={() => onOpenRef(r.ref)} className="text-sm text-slate-200 hover:text-sky-200">
                {r.ref}
              </button>
            ))}
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
          <div className="grid gap-2 sm:grid-cols-2">
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
