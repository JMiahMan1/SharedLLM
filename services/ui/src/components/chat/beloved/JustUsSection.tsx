import { useRef, useState, type FormEvent } from 'react';
import { BellRing, Check, ChevronLeft, ChevronRight, Heart, Loader2, Pencil, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { dayLabel, dayNumber, formatDay, longDayLabel, parseDay, type Ymd } from '../../../lib/beloved/dates';
import {
  EVENING_IDEAS,
  NUDGE_CHOICES,
  PRIVATE_REMINDER,
  addMonths,
  countIn,
  daysSince,
  eveningIdea,
  marksOn,
  monthGrid,
  openPlans,
  sinceLabel,
  togetherFromPlan,
} from '../../../lib/beloved/justus';
import type { Opened, PlanRecord, TogetherRecord } from '../../../lib/beloved/records';
import type { ReminderState } from '../../../plugins/belovedReminders';
import { errorText } from '../privateChatModel';
import { wantsNightSky } from '../../../lib/beloved/sky';
import { Card, Field } from './BelovedUi';
import { BelovedWeather } from './BelovedWeather';
import type { BelovedData } from './useBeloved';

const button = 'flex min-h-11 items-center justify-center gap-1.5 rounded-xl px-3 text-sm font-semibold disabled:opacity-50';
const primary = `${button} bg-indigo-600 text-white`;
const quiet = `${button} bg-white/[0.07] text-slate-100 hover:bg-white/[0.12]`;
const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
const LONG_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

/**
 * "Just us": the couple's own calendar. Days they marked, evenings planned, and
 * how long since the last one. Only on this tab: Beloved's home page, the
 * chat bar's heart and every notice and reminder say nothing about it.
 */
export function JustUsSection({
  data,
  today,
  nudge,
  onNudge,
  reminders,
}: {
  data: BelovedData;
  today: Ymd;
  nudge: number;
  onNudge: (days: number) => void;
  reminders: ReminderState | null;
}) {
  const [busy, setBusy] = useState(false);
  const [month, setMonth] = useState({ y: today.y, m: today.m });
  const [selected, setSelected] = useState(formatDay(today));
  const [note, setNote] = useState('');
  const [planDay, setPlanDay] = useState('');
  const [planTime, setPlanTime] = useState('');
  const [planIdea, setPlanIdea] = useState<string | undefined>();
  const [planNote, setPlanNote] = useState('');
  /** The mark whose note is being edited, and the text so far. */
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const swipe = useRef<number | null>(null);
  const todayKey = formatDay(today);
  const since = daysSince(data.view, today);
  const plans = openPlans(data.view);
  const weeks = monthGrid(data.view, month.y, month.m, today);
  const spouseName = data.spouse?.display_name ?? 'your spouse';
  const selectedDay = parseDay(selected);
  const selectedPast = selectedDay ? dayNumber(selectedDay) <= dayNumber(today) : false;
  const onSelected = marksOn(data.view, selected);
  const marksToday = marksOn(data.view, todayKey).length;
  const plansOnSelected = plans.filter((p) => p.record.day === selected);

  const run = async (job: () => Promise<void>, failure: string) => {
    setBusy(true);
    try {
      await job();
    } catch (error) {
      toast.error(errorText(error, failure));
    } finally {
      setBusy(false);
    }
  };

  const mark = (day: string, text = '') =>
    run(async () => {
      await data.save('together', { b: 'together', day, ...(text.trim() ? { note: text.trim().slice(0, 1000) } : {}) });
      setNote('');
      toast.success('Marked');
    }, 'Could not save');

  const plan = (event: FormEvent) => {
    event.preventDefault();
    if (!parseDay(planDay)) return;
    void run(async () => {
      const record: PlanRecord = { b: 'plan', day: planDay };
      if (planTime) record.time = planTime;
      if (planIdea) record.idea = planIdea;
      if (planNote.trim()) record.note = planNote.trim().slice(0, 1000);
      await data.save('plan', record, { notify: true });
      setPlanDay('');
      setPlanTime('');
      setPlanIdea(undefined);
      setPlanNote('');
      toast.success(`Planned. ${spouseName} will see it here.`);
    }, 'Could not save the plan');
  };

  const finish = (p: Opened<PlanRecord>) =>
    run(async () => {
      await data.save('plan', { ...p.record, doneAt: Math.floor(Date.now() / 1000) }, { id: p.id });
      if (!data.view.together.some((t) => t.record.day === p.record.day)) await data.save('together', togetherFromPlan(p.record));
      toast.success('Marked');
    }, 'Could not save');

  const saveNote = (t: Opened<TogetherRecord>, text: string) =>
    run(async () => {
      const trimmed = text.trim().slice(0, 1000);
      const record: TogetherRecord = { b: 'together', day: t.record.day };
      if (trimmed) record.note = trimmed;
      await data.save('together', record, { id: t.id });
      setEditing(null);
      toast.success('Saved');
    }, 'Could not save');

  const cancel = (p: Opened<PlanRecord>) => run(() => data.remove('plan', p.id), 'Could not cancel it');

  const step = (delta: number) => setMonth((current) => addMonths(current, delta));

  return (
    <div className="space-y-4" data-testid="beloved-justus">
      <Card testId="justus-since">
        <span className="text-xs font-semibold uppercase tracking-wide text-indigo-200/80">Last time</span>
        {since === null ? (
          <span className="mt-1 block text-base text-slate-200">Nothing marked yet.</span>
        ) : (
          <span className="mt-1 flex items-baseline gap-2">
            <span className="text-4xl font-bold tabular-nums text-indigo-100" data-testid="justus-since-days">{since}</span>
            <span className="text-sm text-slate-300">{since === 1 ? 'day since' : 'days since'} · {sinceLabel(since)}</span>
          </span>
        )}
        <span className="mt-2 block text-sm text-slate-300" data-testid="justus-counts">
          This month: {countIn(data.view, today, today.y, today.m)} · This year: {countIn(data.view, today, today.y)}
        </span>
      </Card>

      {/* A day can be marked more than once, so this stays open after the first. */}
      <button
        type="button"
        disabled={busy}
        onClick={() => void mark(todayKey)}
        data-testid="justus-today"
        className="flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-indigo-600 to-rose-600 px-4 text-base font-semibold text-white disabled:opacity-60"
      >
        <Heart size={20} className="fill-white" aria-hidden />
        {marksToday ? 'Mark today again' : 'We were together'}
      </button>
      {marksToday > 0 && (
        <p className="-mt-2 text-center text-xs text-slate-300" data-testid="justus-today-count">
          Today is marked{marksToday > 1 ? ` ${marksToday} times` : ''}
        </p>
      )}

      <section aria-label="Our calendar" className="rounded-2xl border border-indigo-300/10 bg-white/[0.03] p-2" data-testid="justus-calendar">
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => step(-1)} aria-label="Previous month" data-testid="justus-prev"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-200 hover:bg-white/10">
            <ChevronLeft size={18} />
          </button>
          <h3 className="min-w-0 flex-1 truncate text-center font-serif text-base font-semibold text-indigo-100" data-testid="justus-month">
            {LONG_MONTHS[month.m - 1]} {month.y}
          </h3>
          <button type="button" onClick={() => step(1)} aria-label="Next month" data-testid="justus-next"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-200 hover:bg-white/10">
            <ChevronRight size={18} />
          </button>
        </div>
        <div
          className="mt-1 touch-pan-y"
          onTouchStart={(e) => {
            swipe.current = e.touches[0]?.clientX ?? null;
          }}
          onTouchEnd={(e) => {
            const start = swipe.current;
            swipe.current = null;
            const end = e.changedTouches[0]?.clientX;
            if (start === null || end === undefined || Math.abs(end - start) < 50) return;
            step(end < start ? 1 : -1);
          }}
        >
          <div className="grid grid-cols-7 text-center text-[11px] font-semibold text-slate-400" aria-hidden>
            {WEEKDAYS.map((d, i) => <span key={i}>{d}</span>)}
          </div>
          <div className="grid grid-cols-7 gap-0.5" role="grid">
            {weeks.flat().map((cell, i) =>
              cell ? (
                <button
                  key={cell.key}
                  type="button"
                  onClick={() => setSelected(cell.key)}
                  aria-pressed={selected === cell.key}
                  aria-label={`${dayLabel(cell.day)}${cell.marks > 1 ? `, marked ${cell.marks} times` : cell.marked ? ', marked' : ''}${cell.planned ? ', planned' : ''}`}
                  data-testid="justus-day"
                  data-day={cell.key}
                  data-marked={cell.marked || undefined}
                  data-marks={cell.marks || undefined}
                  data-planned={cell.planned || undefined}
                  className={`relative flex min-h-11 min-w-0 flex-col items-center justify-center rounded-lg text-sm tabular-nums ${
                    selected === cell.key ? 'bg-indigo-500/30 text-white' : cell.today ? 'text-indigo-100 ring-1 ring-indigo-300/50' : 'text-slate-200 hover:bg-white/5'
                  } ${cell.planned ? 'outline outline-1 -outline-offset-2 outline-rose-300/70' : ''}`}
                >
                  <span>{cell.day.d}</span>
                  {cell.marks === 1 && <Heart size={9} className="fill-rose-400 text-rose-400" aria-hidden />}
                  {cell.marks > 1 && (
                    <span className="flex items-center gap-px text-[9px] font-semibold leading-none text-rose-300" data-testid="justus-day-marks" aria-hidden>
                      <Heart size={9} className="fill-rose-400 text-rose-400" />
                      {cell.marks}
                    </span>
                  )}
                </button>
              ) : (
                <span key={`pad${i}`} aria-hidden />
              ),
            )}
          </div>
        </div>
        <p className="mt-2 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
          <span className="flex items-center gap-1"><Heart size={9} className="fill-rose-400 text-rose-400" aria-hidden /> marked</span>
          <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm border border-rose-300/70" aria-hidden /> planned</span>
        </p>
      </section>

      {selectedDay && (
        <Card testId="justus-selected">
          <span className="text-xs font-semibold uppercase tracking-wide text-indigo-200/80">
            {longDayLabel(selectedDay)}
            {onSelected.length > 1 && <span className="ml-1 normal-case text-rose-200" data-testid="justus-selected-count">· marked {onSelected.length} times</span>}
          </span>
          {dayNumber(selectedDay) >= dayNumber(today) && <BelovedWeather day={selectedDay} night />}
          <ul className="mt-1 space-y-1.5">
            {onSelected.map((t) => (
              <li key={t.id} className="flex min-w-0 items-start gap-2 text-sm text-slate-100" data-testid="justus-entry">
                <Heart size={14} className="mt-1 shrink-0 fill-rose-400 text-rose-400" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block text-xs text-slate-400">{t.own ? 'You marked it' : `${t.byName} marked it`}</span>
                  {editing?.id === t.id ? (
                    <span className="mt-1 flex min-w-0 flex-wrap gap-2">
                      <input value={editing.text} onChange={(e) => setEditing({ id: t.id, text: e.target.value })} maxLength={1000}
                        aria-label="Edit the note" data-testid="justus-entry-note" autoFocus
                        className="glass-input min-h-11 min-w-0 flex-1 basis-40 text-base sm:text-sm" />
                      <button type="button" disabled={busy} onClick={() => void saveNote(t, editing.text)} data-testid="justus-entry-save" className={quiet}>
                        <Check size={14} aria-hidden /> Save
                      </button>
                      <button type="button" onClick={() => setEditing(null)} data-testid="justus-entry-cancel" className={quiet}>
                        <X size={14} aria-hidden /> Cancel
                      </button>
                    </span>
                  ) : (
                    t.record.note && <span className="block whitespace-pre-wrap break-words">{t.record.note}</span>
                  )}
                </span>
                {t.own && editing?.id !== t.id && (
                  <>
                    <button type="button" aria-label="Edit the note" disabled={busy} data-testid="justus-entry-edit"
                      onClick={() => setEditing({ id: t.id, text: t.record.note ?? '' })}
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10">
                      <Pencil size={15} />
                    </button>
                    <button type="button" aria-label="Remove this" disabled={busy} data-testid="justus-entry-delete"
                      onClick={() => void run(() => data.remove('together', t.id), 'Could not remove it')}
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10">
                      <Trash2 size={15} />
                    </button>
                  </>
                )}
              </li>
            ))}
            {plansOnSelected.map((p) => (
              <li key={p.id} className="text-sm text-rose-100">Planned: {eveningIdea(p.record.idea)?.title ?? 'an evening for the two of you'}</li>
            ))}
            {!onSelected.length && !plansOnSelected.length && <li className="text-sm text-slate-400">Nothing on this day.</li>}
          </ul>
          {selectedPast ? (
            <div className="mt-3 space-y-2">
              <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} placeholder="A private note (optional)"
                aria-label="A private note" data-testid="justus-note" className="glass-input min-h-11 w-full text-base sm:text-sm" />
              <button type="button" disabled={busy} onClick={() => void mark(selected, note)} data-testid="justus-mark" className={`${quiet} w-full`}>
                <Check size={15} aria-hidden /> {onSelected.length ? 'Mark this day again' : 'Mark this day'}
              </button>
            </div>
          ) : (
            <button type="button" onClick={() => setPlanDay(selected)} data-testid="justus-plan-this" className={`${quiet} mt-3 w-full`}>
              Plan this day
            </button>
          )}
        </Card>
      )}

      <section>
        <h3 className="mb-2 mt-1 font-serif text-base font-semibold text-indigo-100">Planned</h3>
        <ul className="space-y-2">
          {plans.map((p) => {
            const day = parseDay(p.record.day) as Ymd;
            const due = dayNumber(day) <= dayNumber(today);
            return (
              <li key={p.id} className="rounded-2xl border border-rose-300/15 bg-rose-500/[0.07] p-3" data-testid="justus-plan">
                <span className="block break-words font-semibold text-white">{eveningIdea(p.record.idea)?.title ?? 'An evening for the two of you'}</span>
                <span className="block text-xs text-slate-300">
                  {dayLabel(day)}{p.record.time ? ` at ${p.record.time}` : ''} · {p.own ? 'You planned it' : `${p.byName} planned it`}
                </span>
                <BelovedWeather day={day} night={wantsNightSky(p.record.time, p.record.idea)} />
                {p.record.note && <span className="mt-1 block whitespace-pre-wrap break-words text-sm text-slate-100">{p.record.note}</span>}
                <div className="mt-2 flex flex-wrap gap-2">
                  <button type="button" disabled={busy || !due} onClick={() => void finish(p)} data-testid="justus-plan-done" className={quiet}>
                    <Check size={14} aria-hidden /> Done
                  </button>
                  <button type="button" disabled={busy} onClick={() => void cancel(p)} data-testid="justus-plan-cancel" className={quiet}>
                    <X size={14} aria-hidden /> Cancel
                  </button>
                </div>
              </li>
            );
          })}
          {!plans.length && <li className="py-2 text-center text-sm text-slate-400">Nothing planned yet.</li>}
        </ul>
        <form onSubmit={plan} className="mt-3 space-y-3 rounded-2xl border border-white/10 bg-white/[0.03] p-3" data-testid="justus-plan-form">
          <p className="text-sm font-semibold text-indigo-100">Plan a special evening</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Day">
              <input type="date" value={planDay} min={todayKey} onChange={(e) => setPlanDay(e.target.value)} data-testid="justus-plan-day"
                className="glass-input min-h-11 w-full text-base sm:text-sm" />
            </Field>
            <Field label="Time (optional)">
              <input type="time" value={planTime} onChange={(e) => setPlanTime(e.target.value)} data-testid="justus-plan-time"
                className="glass-input min-h-11 w-full text-base sm:text-sm" />
            </Field>
          </div>
          {parseDay(planDay) && (
            <div data-testid="justus-plan-weather">
              <BelovedWeather day={parseDay(planDay) as Ymd} night={wantsNightSky(planTime || undefined, planIdea)} />
            </div>
          )}
          <div>
            <span className="mb-1 block text-xs font-semibold text-slate-300">An idea (optional)</span>
            <div className="flex flex-wrap gap-1.5">
              {EVENING_IDEAS.map((idea) => (
                <button key={idea.id} type="button" aria-pressed={planIdea === idea.id} data-testid="justus-idea"
                  onClick={() => setPlanIdea(planIdea === idea.id ? undefined : idea.id)}
                  className={`min-h-11 rounded-full border px-3 text-left text-sm ${
                    planIdea === idea.id ? 'border-rose-300/60 bg-rose-500/25 text-white' : 'border-white/10 bg-white/[0.04] text-slate-200'
                  }`}>
                  {idea.title}
                </button>
              ))}
            </div>
          </div>
          <Field label="A note (optional)">
            <input value={planNote} onChange={(e) => setPlanNote(e.target.value)} maxLength={1000} data-testid="justus-plan-note"
              className="glass-input min-h-11 w-full text-base sm:text-sm" />
          </Field>
          <button type="submit" disabled={busy || !parseDay(planDay)} data-testid="justus-plan-add" className={`${primary} w-full`}>
            {busy ? <Loader2 size={15} className="animate-spin" /> : <Heart size={15} aria-hidden />} Plan it
          </button>
          <p className="text-xs text-slate-400">
            {spouseName} gets a quiet notice that only says something is waiting. Both phones get a reminder that morning.
          </p>
        </form>
      </section>

      <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-3" data-testid="justus-reminder">
        <label className="flex min-h-11 flex-wrap items-center gap-2 text-sm text-slate-200">
          <BellRing size={15} className="shrink-0" aria-hidden />
          <span className="min-w-0 flex-1">Remind me after</span>
          <select value={nudge} onChange={(e) => onNudge(Number(e.target.value))} data-testid="justus-nudge"
            className="glass-input min-h-11 text-base sm:text-sm">
            {NUDGE_CHOICES.map((d) => <option key={d} value={d}>{d ? `${d} days` : 'Off'}</option>)}
          </select>
        </label>
        <p className="mt-1 text-xs text-slate-400">
          On this phone only, counted from the last marked day. The reminder only says "{PRIVATE_REMINDER}"
        </p>
        {reminders !== 'on' && (
          <p className="mt-1 text-xs text-slate-400" data-testid="justus-reminder-state">
            {reminders === 'unsupported' ? 'Update the Jarvis app to get reminders.' : 'Reminders come on the Jarvis Android app.'}
          </p>
        )}
      </section>
    </div>
  );
}
