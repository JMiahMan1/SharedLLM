import { useState, type FormEvent, type ReactNode } from 'react';
import { BellRing, Check, CheckCircle2, HandHeart, Heart, Loader2, Plus, Send, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { countdownLabel, dayLabel, localToday, type Ymd } from '../../../lib/beloved/dates';
import { DATE_IDEAS, dailyContent, dateIdea } from '../../../lib/beloved/content';
import type { SpecialDay } from '../../../lib/beloved/days';
import { markPrayed, prayedCount, type DevotionRecord, type IdeaRecord, type Opened, type PrayerRecord } from '../../../lib/beloved/records';
import type { ReminderState } from '../../../plugins/belovedReminders';
import { stampLabel } from '../privateGalleryModel';
import { errorText } from '../privateChatModel';
import { Pomegranate, Vine } from './BelovedArt';
import { Card, Field } from './BelovedUi';
import { useVerse } from './useBeloved';
import type { BelovedData } from './useBeloved';

const button = 'flex min-h-11 items-center justify-center gap-1.5 rounded-xl px-3 text-sm font-semibold disabled:opacity-50';
const primary = `${button} bg-rose-600 text-white`;
const quiet = `${button} bg-white/[0.07] text-slate-100 hover:bg-white/[0.12]`;

function useBusy(): [boolean, (job: () => Promise<void>, failure: string) => Promise<void>] {
  const [busy, setBusy] = useState(false);
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
  return [busy, run];
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="mb-2 mt-1 font-serif text-base font-semibold text-rose-100">{children}</h3>;
}

// --- Special days -----------------------------------------------------------------

export function DaysSection({ data, days, reminders }: { data: BelovedData; days: SpecialDay[]; reminders: ReminderState | null }) {
  const [name, setName] = useState('');
  const [date, setDate] = useState('');
  const [yearly, setYearly] = useState(true);
  const [busy, run] = useBusy();

  const add = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !date) return;
    void run(async () => {
      await data.save('day', { b: 'day', name: name.trim().slice(0, 120), date, yearly });
      setName('');
      setDate('');
      toast.success('Added');
    }, 'Could not add the day');
  };

  return (
    <div className="space-y-4" data-testid="beloved-days">
      <Heading>Special days</Heading>
      {!data.view.profile?.record.wedding && (
        <p className="rounded-2xl border border-rose-300/15 bg-rose-500/10 p-3 text-sm text-rose-100">
          Add your wedding date in Our details to count your days and see your milestones.
        </p>
      )}
      <ul className="space-y-2">
        {days.map((day) => (
          <li key={day.key} className="flex min-w-0 items-center gap-3 rounded-2xl border border-rose-300/10 bg-white/[0.04] p-3" data-testid="beloved-day">
            <span className={`flex h-12 w-12 shrink-0 flex-col items-center justify-center rounded-xl text-center leading-none ${
              day.inDays === 0 ? 'bg-rose-500 text-white' : 'bg-rose-500/15 text-rose-100'
            }`}>
              <span className="text-base font-bold tabular-nums">{day.inDays > 999 ? '999+' : day.inDays}</span>
              <span className="text-[10px]">{day.inDays === 1 ? 'day' : 'days'}</span>
            </span>
            <span className="min-w-0 flex-1">
              <span className="block break-words font-semibold text-white">{day.name}</span>
              <span className="block text-xs text-slate-400">{countdownLabel(day.inDays)} · {dayLabel(day.date)}</span>
            </span>
            {day.recordId && (
              <button
                type="button"
                aria-label={`Remove ${day.name}`}
                onClick={() => void run(() => data.remove('day', day.recordId as string), 'Could not remove it')}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-400 hover:bg-white/10"
              >
                <Trash2 size={16} />
              </button>
            )}
          </li>
        ))}
        {!days.length && <li className="py-6 text-center text-sm text-slate-400">No special days yet.</li>}
      </ul>
      <form onSubmit={add} className="space-y-3 rounded-2xl border border-white/10 bg-white/[0.03] p-3" data-testid="beloved-day-form">
        <p className="text-sm font-semibold text-rose-100">Add a day of your own</p>
        <Field label="Name">
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="The day we got the keys" data-testid="beloved-day-name"
            className="glass-input min-h-11 w-full text-base sm:text-sm" />
        </Field>
        <Field label="Date">
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} data-testid="beloved-day-date" className="glass-input min-h-11 w-full text-base sm:text-sm" />
        </Field>
        <label className="flex min-h-11 items-center gap-3 text-sm text-slate-200">
          <input type="checkbox" checked={yearly} onChange={(e) => setYearly(e.target.checked)} className="h-5 w-5" /> Every year
        </label>
        <button type="submit" disabled={busy || !name.trim() || !date} data-testid="beloved-day-add" className={`${primary} w-full`}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />} Add day
        </button>
      </form>
      <p className="flex items-start gap-2 text-xs text-slate-400">
        <BellRing size={14} className="mt-0.5 shrink-0" aria-hidden />
        {reminders === 'on'
          ? 'This phone reminds you the evening before and the morning of each day.'
          : 'Reminders come on the Jarvis Android app; the server never knows your dates.'}
      </p>
    </div>
  );
}

// --- Today: verse, devotion, question ----------------------------------------------

export function TodaySection({ data, today }: { data: BelovedData; today: Ymd }) {
  const content = dailyContent(today);
  const verse = useVerse(content.devotion.ref);
  const mine = data.view.devotions.find((d) => d.own && d.record.day === content.day) as Opened<DevotionRecord> | undefined;
  const theirs = data.view.devotions.find((d) => !d.own && d.record.day === content.day) as Opened<DevotionRecord> | undefined;
  const [answer, setAnswer] = useState(mine?.record.answer ?? '');
  const [busy, run] = useBusy();

  const store = (next: DevotionRecord) =>
    run(async () => {
      await data.save('devotion', next, mine ? { id: mine.id } : {});
    }, 'Could not save');

  return (
    <div className="space-y-4" data-testid="beloved-today">
      <Card>
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-200/80">{dayLabel(today)}</span>
        <h3 className="mt-1 font-serif text-xl font-semibold text-white">{content.devotion.title}</h3>
        <blockquote className="mt-3 border-l-2 border-rose-400/50 pl-3 font-serif text-[15px] leading-relaxed text-rose-50" data-testid="beloved-verse-text">
          {verse.loading ? 'Loading…' : verse.failed ? 'The verse could not be loaded; open the Bible to read it.' : verse.text}
          <cite className="mt-1 block text-xs font-semibold not-italic text-rose-200">{content.devotion.ref}</cite>
        </blockquote>
        <p className="mt-3 text-sm leading-relaxed text-slate-200" data-testid="beloved-reflection">{content.devotion.reflection}</p>
        <p className="mt-3 text-sm italic text-rose-100">Pray together: {content.devotion.prayer}</p>
      </Card>
      <Vine className="text-rose-300/30" />
      <Card>
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-200/80">Talk about it together</span>
        <p className="mt-1 break-words text-base font-semibold text-white" data-testid="beloved-question">{content.question}</p>
        <Field label="Your answer (only the two of you see it)">
          <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={3} maxLength={4000} data-testid="beloved-answer"
            className="glass-input mt-2 w-full resize-y text-base sm:text-sm" />
        </Field>
        <div className="mt-2 flex flex-wrap gap-2">
          <button type="button" disabled={busy} data-testid="beloved-devotion-done" className={mine?.record.done ? quiet : primary}
            onClick={() => void store({ b: 'devotion', day: content.day, done: !mine?.record.done, ...(answer.trim() ? { answer: answer.trim() } : {}) })}>
            {mine?.record.done ? <><CheckCircle2 size={15} aria-hidden /> Done today</> : <><Check size={15} aria-hidden /> Mark done</>}
          </button>
          <button type="button" disabled={busy || !answer.trim()} data-testid="beloved-answer-save" className={quiet}
            onClick={() => void store({ b: 'devotion', day: content.day, done: mine?.record.done ?? false, answer: answer.trim() })}>
            Save answer
          </button>
        </div>
        {theirs && (
          <div className="mt-3 rounded-xl bg-rose-500/10 p-3 text-sm text-rose-50" data-testid="beloved-their-answer">
            <span className="block text-xs font-semibold text-rose-200">
              {theirs.byName}{theirs.record.done ? ' did today\'s devotion' : ''}
            </span>
            {theirs.record.answer && <span className="mt-1 block whitespace-pre-wrap break-words">{theirs.record.answer}</span>}
          </div>
        )}
      </Card>
    </div>
  );
}

// --- Love notes and prayer -----------------------------------------------------------

export function NotesSection({ data }: { data: BelovedData }) {
  const [note, setNote] = useState('');
  const [prayer, setPrayer] = useState('');
  const [prayerFor, setPrayerFor] = useState(data.me);
  const [answering, setAnswering] = useState<string | null>(null);
  const [answerText, setAnswerText] = useState('');
  const [busy, run] = useBusy();
  const spouseName = data.spouse?.display_name ?? 'your spouse';
  const open = data.view.prayers.filter((p) => !p.record.answeredAt);
  const answered = data.view.prayers.filter((p) => p.record.answeredAt);

  const sendNote = (event: FormEvent) => {
    event.preventDefault();
    if (!note.trim()) return;
    void run(async () => {
      await data.save('note', { b: 'note', text: note.trim().slice(0, 4000) }, { notify: true });
      setNote('');
      toast.success(`Sent to ${spouseName}`);
    }, 'Could not send the note');
  };

  const addPrayer = (event: FormEvent) => {
    event.preventDefault();
    if (!prayer.trim()) return;
    void run(async () => {
      await data.save('prayer', { b: 'prayer', text: prayer.trim().slice(0, 4000), ...(prayerFor ? { for: prayerFor } : {}) }, { notify: true });
      setPrayer('');
    }, 'Could not share the prayer request');
  };

  const prayed = (p: Opened<PrayerRecord>) =>
    run(async () => {
      await data.save('prayer', markPrayed(p.record, data.me, Date.now() / 1000), { id: p.id });
      toast.success('Prayed');
    }, 'Could not save');

  const markAnswered = (p: Opened<PrayerRecord>) =>
    run(async () => {
      await data.save('prayer', { ...p.record, answeredAt: Math.floor(Date.now() / 1000), ...(answerText.trim() ? { answer: answerText.trim() } : {}) }, { id: p.id });
      setAnswering(null);
      setAnswerText('');
      toast.success('Praise God!');
    }, 'Could not save');

  const whoFor = (p: PrayerRecord) => data.couple.find((c) => c.username === p.for)?.display_name;

  return (
    <div className="space-y-5" data-testid="beloved-notes">
      <section>
        <Heading>Love notes</Heading>
        <form onSubmit={sendNote} className="space-y-2">
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={4000} placeholder={`Write something for ${spouseName}…`}
            aria-label="Love note" data-testid="beloved-note-input" className="glass-input w-full resize-y text-base sm:text-sm" />
          <button type="submit" disabled={busy || !note.trim()} data-testid="beloved-note-send" className={`${primary} w-full`}>
            <Send size={15} aria-hidden /> Send note
          </button>
        </form>
        <ul className="mt-3 space-y-2">
          {data.view.notes.slice(0, 30).map((n) => (
            <li key={n.id} data-testid="beloved-note"
              className={`min-w-0 rounded-2xl p-3 text-sm ${n.own ? 'ml-6 bg-gradient-to-br from-rose-600/40 to-fuchsia-600/30 text-white' : 'mr-6 border border-rose-300/15 bg-white/[0.04] text-slate-100'}`}>
              <span className="block whitespace-pre-wrap break-words">{n.record.b === 'note' ? n.record.text : ''}</span>
              <span className="mt-1 flex items-center justify-between gap-2 text-[11px] text-slate-300">
                <span className="min-w-0 truncate">{n.own ? 'You' : n.byName} · {stampLabel(n.at)}</span>
                {n.own && (
                  <button type="button" aria-label="Delete note" onClick={() => void run(() => data.remove('note', n.id), 'Could not delete')}
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-slate-300 hover:bg-white/10">
                    <Trash2 size={14} />
                  </button>
                )}
              </span>
            </li>
          ))}
          {!data.view.notes.length && <li className="py-3 text-center text-sm text-slate-400">No notes yet. Leave the first one.</li>}
        </ul>
      </section>

      <Vine className="text-rose-300/30" />

      <section>
        <Heading>Praying for each other</Heading>
        <form onSubmit={addPrayer} className="space-y-2">
          <textarea value={prayer} onChange={(e) => setPrayer(e.target.value)} rows={2} maxLength={4000} placeholder="What can we pray about?"
            aria-label="Prayer request" data-testid="beloved-prayer-input" className="glass-input w-full resize-y text-base sm:text-sm" />
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex min-h-11 min-w-0 flex-1 items-center gap-2 text-sm text-slate-300">
              For
              <select value={prayerFor} onChange={(e) => setPrayerFor(e.target.value)} data-testid="beloved-prayer-for" className="glass-input min-h-11 min-w-0 flex-1 text-base sm:text-sm">
                {data.couple.map((c) => (
                  <option key={c.username} value={c.username}>{c.me ? 'Me' : c.display_name}</option>
                ))}
                <option value="">Both of us</option>
              </select>
            </label>
            <button type="submit" disabled={busy || !prayer.trim()} data-testid="beloved-prayer-add" className={primary}>
              <HandHeart size={15} aria-hidden /> Share
            </button>
          </div>
        </form>
        <ul className="mt-3 space-y-2">
          {open.map((p) => (
            <li key={p.id} className="rounded-2xl border border-rose-300/10 bg-white/[0.04] p-3" data-testid="beloved-prayer">
              <span className="block whitespace-pre-wrap break-words text-sm text-slate-100">{p.record.text}</span>
              <span className="mt-1 block text-[11px] text-slate-400">
                {p.own ? 'You' : p.byName}{whoFor(p.record) ? ` · for ${whoFor(p.record)}` : ' · for both of you'}
                {prayedCount(p.record) > 0 && ` · prayed ${prayedCount(p.record)} ${prayedCount(p.record) === 1 ? 'time' : 'times'}`}
              </span>
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" disabled={busy} onClick={() => void prayed(p)} data-testid="beloved-prayed" className={quiet}>
                  <Heart size={14} aria-hidden /> Prayed for this
                </button>
                <button type="button" disabled={busy} onClick={() => setAnswering(answering === p.id ? null : p.id)} data-testid="beloved-answered-open" className={quiet}>
                  <CheckCircle2 size={14} aria-hidden /> Answered
                </button>
              </div>
              {answering === p.id && (
                <div className="mt-2 space-y-2">
                  <input value={answerText} onChange={(e) => setAnswerText(e.target.value)} placeholder="How did God answer? (optional)" maxLength={4000}
                    aria-label="How it was answered" data-testid="beloved-answer-how" className="glass-input min-h-11 w-full text-base sm:text-sm" />
                  <button type="button" disabled={busy} onClick={() => void markAnswered(p)} data-testid="beloved-answered-save" className={`${primary} w-full`}>
                    Mark answered
                  </button>
                </div>
              )}
            </li>
          ))}
          {!open.length && <li className="py-3 text-center text-sm text-slate-400">No open prayer requests.</li>}
        </ul>
        {answered.length > 0 && (
          <div className="mt-4" data-testid="beloved-answered">
            <p className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-amber-100">
              <Pomegranate size={16} /> Answered prayers ({answered.length})
            </p>
            <ul className="space-y-2">
              {answered.map((p) => (
                <li key={p.id} className="rounded-2xl border border-amber-300/15 bg-amber-400/[0.06] p-3 text-sm text-slate-100" data-testid="beloved-answered-prayer">
                  <span className="block whitespace-pre-wrap break-words">{p.record.text}</span>
                  {p.record.answer && <span className="mt-1 block whitespace-pre-wrap break-words text-amber-100">{p.record.answer}</span>}
                  <span className="mt-1 block text-[11px] text-slate-400">Answered {stampLabel(p.record.answeredAt ?? p.at)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}

// --- Date night ideas -----------------------------------------------------------------

const KIND_LABEL: Record<string, string> = { home: 'At home', out: 'Out', outdoors: 'Outdoors', serve: 'Serve together', faith: 'Faith' };

export function DatesSection({ data }: { data: BelovedData }) {
  const [busy, run] = useBusy();
  const [custom, setCustom] = useState('');
  const [showAll, setShowAll] = useState(false);
  const spouseName = data.spouse?.display_name ?? 'your spouse';
  const suggested = data.view.ideas.filter((i) => !i.record.doneAt);
  const done = data.view.ideas.filter((i) => i.record.doneAt);
  const taken = new Set(suggested.map((i) => i.record.idea).filter(Boolean));
  // A fresh handful each day, the same on both phones.
  const offset = (dailyContent(localToday()).day.split('-').reduce((a, b) => a + Number(b), 0) * 7) % DATE_IDEAS.length;
  const rotated = [...DATE_IDEAS.slice(offset), ...DATE_IDEAS.slice(0, offset)].filter((i) => !taken.has(i.id));
  const list = showAll ? rotated : rotated.slice(0, 8);

  const suggest = (record: IdeaRecord) =>
    run(async () => {
      await data.save('idea', record, { notify: true });
      toast.success(`Suggested to ${spouseName}`);
    }, 'Could not suggest it');

  const title = (r: IdeaRecord) => (r.idea ? dateIdea(r.idea)?.title : undefined) ?? r.custom ?? 'A date';

  return (
    <div className="space-y-5" data-testid="beloved-dates">
      {suggested.length > 0 && (
        <section>
          <Heading>Suggested</Heading>
          <ul className="space-y-2">
            {suggested.map((i) => (
              <li key={i.id} className="flex min-w-0 items-center gap-3 rounded-2xl border border-rose-300/15 bg-rose-500/10 p-3" data-testid="beloved-suggested">
                <span className="min-w-0 flex-1">
                  <span className="block break-words font-semibold text-white">{title(i.record)}</span>
                  <span className="block text-xs text-slate-300">{i.own ? `You suggested it` : `${i.byName} suggested it`} · {stampLabel(i.at)}</span>
                </span>
                <button type="button" disabled={busy} data-testid="beloved-idea-done" className={quiet}
                  onClick={() => void run(() => data.save('idea', { ...i.record, doneAt: Math.floor(Date.now() / 1000) }, { id: i.id }).then(() => undefined), 'Could not save')}>
                  <Check size={14} aria-hidden /> Done
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section>
        <Heading>Date night ideas</Heading>
        <ul className="grid gap-2 sm:grid-cols-2">
          {list.map((idea) => (
            <li key={idea.id} className="flex min-w-0 flex-col rounded-2xl border border-white/10 bg-white/[0.04] p-3" data-testid="beloved-idea">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-amber-200/80">{KIND_LABEL[idea.kind]}</span>
              <span className="mt-0.5 break-words font-semibold text-white">{idea.title}</span>
              <span className="mt-1 flex-1 break-words text-sm text-slate-300">{idea.detail}</span>
              <button type="button" disabled={busy} onClick={() => void suggest({ b: 'idea', idea: idea.id })} data-testid="beloved-idea-suggest" className={`${quiet} mt-2`}>
                <Heart size={14} aria-hidden /> Suggest to {spouseName}
              </button>
            </li>
          ))}
        </ul>
        {!showAll && rotated.length > list.length && (
          <button type="button" onClick={() => setShowAll(true)} className={`${quiet} mt-2 w-full`} data-testid="beloved-ideas-more">
            Show all {rotated.length} ideas
          </button>
        )}
        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (!custom.trim()) return;
            void suggest({ b: 'idea', custom: custom.trim().slice(0, 200) }).then(() => setCustom(''));
          }}
        >
          <input value={custom} onChange={(e) => setCustom(e.target.value)} maxLength={200} placeholder="Your own idea" aria-label="Your own date idea"
            className="glass-input min-h-11 min-w-0 flex-1 text-base sm:text-sm" />
          <button type="submit" disabled={busy || !custom.trim()} className={primary} aria-label="Suggest your idea">
            <Send size={15} />
          </button>
        </form>
      </section>
      {done.length > 0 && (
        <section>
          <Heading>Dates we have had ({done.length})</Heading>
          <ul className="space-y-1.5">
            {done.map((i) => (
              <li key={i.id} className="flex items-center gap-2 text-sm text-slate-300" data-testid="beloved-idea-had">
                <CheckCircle2 size={14} className="shrink-0 text-emerald-300" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{title(i.record)}</span>
                <span className="shrink-0 text-xs text-slate-500">{stampLabel(i.record.doneAt ?? i.at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
