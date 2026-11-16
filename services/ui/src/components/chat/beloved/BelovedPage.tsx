import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { BookHeart, CalendarHeart, Heart, HeartHandshake, ImagePlus, Loader2, NotebookPen, Settings2, Smartphone, Sparkles, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../../services/api';
import { IMAGE_TYPES, type PrivateVault } from '../../../lib/privateChatCrypto';
import { pickerDone, pickerOpening } from '../../../lib/privateChatSession';
import { countdownLabel, dayLabel, dayNumber, durationLabel, elapsed, grouped, localToday, parseDay, upcomingMilestones } from '../../../lib/beloved/dates';
import { dailyContent } from '../../../lib/beloved/content';
import { specialDays } from '../../../lib/beloved/days';
import { planReminders, reminderDetails, setReminderDetails } from '../../../lib/beloved/reminders';
import type { ProfileRecord } from '../../../lib/beloved/records';
import { syncReminders, type ReminderState } from '../../../plugins/belovedReminders';
import UserAvatar from '../../UserAvatar';
import { MAX_ATTACHMENT_BYTES } from '../chatModel';
import { errorText } from '../privateChatModel';
import { noSave, usePrivatePicture } from '../usePrivatePicture';
import { Lily, Pomegranate, Vine } from './BelovedArt';
import { DatesSection, DaysSection, NotesSection, TodaySection } from './BelovedSections';
import { Card, Field } from './BelovedUi';
import { useBeloved, useVerse, type BelovedData } from './useBeloved';

type Tab = 'home' | 'days' | 'today' | 'notes' | 'dates';

const TABS: ReadonlyArray<{ id: Tab; label: string; icon: typeof Heart }> = [
  { id: 'home', label: 'Us', icon: Heart },
  { id: 'days', label: 'Days', icon: CalendarHeart },
  { id: 'today', label: 'Today', icon: BookHeart },
  { id: 'notes', label: 'Notes', icon: NotebookPen },
  { id: 'dates', label: 'Dates', icon: HeartHandshake },
];

/**
 * Beloved: the couple's page inside the private chat ("I am my beloved's, and
 * my beloved is mine", Song of Solomon 6:3). Shown only while the chat is
 * unlocked (it lives inside the unlocked chat, so a lock takes it away), and
 * only to the two spouses. Everything personal on it is sealed on this phone
 * with the chat's key before it is sent; see docs/BELOVED.md.
 */
export default function BelovedPage({
  token,
  vault,
  onClose,
  onAddToHome,
}: {
  token: string;
  vault: PrivateVault;
  onClose: () => void;
  /** Only in the Android app: a home-screen icon with your spouse's picture that opens straight here. */
  onAddToHome?: () => void;
}) {
  const data = useBeloved(token, vault);
  const [tab, setTab] = useState<Tab>('home');
  const [editing, setEditing] = useState(false);
  const [reminders, setReminders] = useState<ReminderState | null>(null);
  const today = localToday();
  const todayKey = `${today.y}-${today.m}-${today.d}`;
  const days = useMemo(
    () => specialDays(data.view, data.couple, localToday()),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- recomputed per calendar day, not per render
    [data.view, data.couple, todayKey],
  );

  // Reminders are scheduled on this phone from what it just decrypted (the server cannot read the dates).
  useEffect(() => {
    if (data.loading) return;
    let live = true;
    void syncReminders(planReminders(days, Date.now(), reminderDetails())).then((state) => {
      if (live) setReminders(state);
    });
    return () => {
      live = false;
    };
  }, [days, data.loading]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !editing) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [editing, onClose]);

  const notFound = (data.error as { response?: { status?: number } } | null)?.response?.status === 404;

  return (
    <div
      className="fixed inset-0 z-[85] flex flex-col bg-gradient-to-b from-[#1a0b16] via-slate-950 to-slate-950"
      role="dialog"
      aria-modal="true"
      aria-label="Beloved"
      data-testid="beloved"
      {...noSave}
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-rose-300/10 px-2 pb-1 pt-[max(0.5rem,env(safe-area-inset-top))]">
        <button type="button" onClick={onClose} aria-label="Close Beloved" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-200 hover:bg-white/10">
          <X size={20} />
        </button>
        <h2 className="flex min-w-0 flex-1 items-center gap-1.5 truncate font-serif text-lg font-semibold text-rose-100">
          <Lily size={18} className="shrink-0 text-rose-200" /> Beloved
        </h2>
        {onAddToHome && (
          <button
            type="button"
            onClick={onAddToHome}
            aria-label="Add Beloved to home screen"
            title="Add Beloved to home screen"
            data-testid="beloved-home-shortcut"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-rose-100 hover:bg-white/10"
          >
            <Smartphone size={18} />
          </button>
        )}
        <button
          type="button"
          onClick={() => setEditing(true)}
          disabled={data.loading || Boolean(data.error)}
          aria-label="Our details"
          title="Our details"
          data-testid="beloved-settings-open"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-rose-100 hover:bg-white/10 disabled:opacity-40"
        >
          <Settings2 size={18} />
        </button>
      </div>
      <nav className="grid shrink-0 grid-cols-5 gap-1 border-b border-rose-300/10 px-1.5 py-1" aria-label="Beloved sections">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-current={tab === id ? 'page' : undefined}
            data-testid={`beloved-tab-${id}`}
            className={`flex min-h-11 min-w-0 flex-col items-center justify-center gap-0.5 rounded-xl px-1 text-[11px] font-semibold ${
              tab === id ? 'bg-rose-500/20 text-rose-100' : 'text-slate-300 hover:bg-white/5'
            }`}
          >
            <Icon size={16} aria-hidden />
            <span className="max-w-full truncate">{label}</span>
          </button>
        ))}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-3 sm:px-4">
        <div className="mx-auto w-full max-w-2xl">
          {notFound ? (
            <p className="rounded-2xl border border-white/10 bg-white/5 p-4 text-center text-sm text-slate-300" role="alert">
              Not available.
            </p>
          ) : data.error ? (
            <p className="rounded-2xl border border-amber-400/20 bg-amber-500/10 p-4 text-sm text-amber-100" role="alert">
              {errorText(data.error, 'Could not open Beloved; try again.')}
            </p>
          ) : data.loading ? (
            <p className="flex items-center justify-center gap-2 py-16 text-sm text-slate-400">
              <Loader2 size={16} className="animate-spin" /> Opening…
            </p>
          ) : tab === 'home' ? (
            <HomeSection data={data} token={token} vault={vault} onEdit={() => setEditing(true)} onTab={setTab} upcoming={days} />
          ) : tab === 'days' ? (
            <DaysSection data={data} days={days} reminders={reminders} />
          ) : tab === 'today' ? (
            <TodaySection data={data} today={today} />
          ) : tab === 'notes' ? (
            <NotesSection data={data} />
          ) : (
            <DatesSection data={data} />
          )}
        </div>
      </div>
      {editing && <ProfileSheet data={data} token={token} vault={vault} reminders={reminders} onClose={() => setEditing(false)} />}
    </div>
  );
}

function Cover({ token, vault, cover }: { token: string; vault: PrivateVault; cover?: ProfileRecord['cover'] }) {
  if (!cover) {
    return (
      <div className="flex h-36 items-center justify-center gap-4 rounded-3xl border border-rose-300/10 bg-gradient-to-br from-rose-500/20 via-fuchsia-500/10 to-amber-400/10 text-rose-200/70 sm:h-44">
        <Lily size={34} />
        <Pomegranate size={34} />
        <Lily size={34} />
      </div>
    );
  }
  return <CoverPhoto token={token} vault={vault} blob={cover.blob} mime={cover.mime} />;
}

function CoverPhoto({ token, vault, blob, mime }: { token: string; vault: PrivateVault; blob: string; mime: string }) {
  const { url, failed } = usePrivatePicture(token, blob, mime, vault);
  return (
    <div className="relative h-40 overflow-hidden rounded-3xl border border-rose-300/10 bg-black/30 sm:h-56">
      {url ? (
        <img src={url} alt="Our cover photo" {...noSave} data-testid="beloved-cover"
          className="pointer-events-none h-full w-full select-none object-cover [-webkit-touch-callout:none]" />
      ) : (
        <span className="flex h-full items-center justify-center text-xs text-slate-400">
          {failed ? 'The cover photo could not be opened.' : <Loader2 size={18} className="animate-spin" />}
        </span>
      )}
      <span className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-slate-950/80 to-transparent" />
    </div>
  );
}

function HomeSection({
  data,
  token,
  vault,
  onEdit,
  onTab,
  upcoming,
}: {
  data: BelovedData;
  token: string;
  vault: PrivateVault;
  onEdit: () => void;
  onTab: (tab: Tab) => void;
  upcoming: ReturnType<typeof specialDays>;
}) {
  const profile = data.view.profile?.record;
  const today = localToday();
  const wedding = parseDay(profile?.wedding);
  const span = wedding ? elapsed(wedding, today) : null;
  const next = wedding ? upcomingMilestones(wedding, today, 1)[0] : undefined;
  const content = dailyContent(today);
  const verse = useVerse(content.devotion.ref);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const me = data.couple.find((p) => p.me);
  const spouse = data.spouse;
  const latestNote = data.view.notes.find((n) => !n.own);
  const fromSpouse = data.view.hearts.filter((h) => !h.own);
  const nextDay = upcoming.find((d) => d.kind !== 'milestone') ?? upcoming[0];

  const heart = async () => {
    setSending(true);
    try {
      const { notified } = await data.save('heart', { b: 'heart' }, { notify: true });
      setSent(true);
      window.setTimeout(() => setSent(false), 1800);
      toast.success(notified ? `${spouse?.display_name ?? 'Your spouse'} will feel it` : 'Heart sent');
    } catch (error) {
      toast.error(errorText(error, 'Could not send the heart'));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="space-y-4" data-testid="beloved-home">
      <Cover token={token} vault={vault} cover={profile?.cover} />
      <div className="-mt-12 flex items-end justify-center gap-3 px-2">
        {me && <UserAvatar username={me.username} avatar={me.avatar} name={me.display_name} size={76} className="ring-4 ring-slate-950" />}
        <Heart size={26} className="mb-6 shrink-0 fill-rose-500 text-rose-400 motion-safe:animate-[nudge-beat_1.6s_ease-in-out_infinite]" aria-hidden />
        {spouse && <UserAvatar username={spouse.username} avatar={spouse.avatar} name={spouse.display_name} size={76} className="ring-4 ring-slate-950" />}
      </div>
      <div className="text-center">
        <p className="break-words font-serif text-xl font-semibold text-white" data-testid="beloved-names">
          {profile?.title || [me?.display_name, spouse?.display_name].filter(Boolean).join(' & ')}
        </p>
        {span ? (
          <div className="mt-2" data-testid="beloved-counter">
            <p className="text-4xl font-bold tabular-nums text-rose-100">{grouped(span.days)}</p>
            <p className="text-sm text-rose-200/80">days married · {durationLabel(span)}</p>
          </div>
        ) : wedding ? (
          <p className="mt-2 text-sm text-rose-100" data-testid="beloved-counter">
            Our wedding day: {countdownLabel(dayNumber(wedding) - dayNumber(today))}
          </p>
        ) : (
          <button type="button" onClick={onEdit} data-testid="beloved-add-wedding"
            className="mt-3 min-h-11 rounded-full border border-rose-300/30 bg-rose-500/10 px-4 text-sm font-semibold text-rose-100">
            Add your wedding date
          </button>
        )}
        <p className="mt-2 text-xs italic text-rose-200/70">I am my beloved's, and my beloved is mine. Song of Solomon 6:3</p>
      </div>
      <Vine className="text-rose-300/40" />

      <button
        type="button"
        onClick={() => void heart()}
        disabled={sending}
        data-testid="beloved-heart"
        className="flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-rose-600 to-fuchsia-600 px-4 text-base font-semibold text-white shadow-lg shadow-rose-900/30 disabled:opacity-60"
      >
        <Heart size={20} className={`fill-white ${sent ? 'motion-safe:animate-[nudge-beat_1.1s_ease-in-out_infinite]' : ''}`} aria-hidden />
        {sent ? 'Sent with love' : 'Thinking of you'}
      </button>
      {fromSpouse.length > 0 && (
        <p className="text-center text-xs text-rose-200/80" data-testid="beloved-hearts-received">
          {spouse?.display_name ?? 'Your spouse'} has sent you {fromSpouse.length} {fromSpouse.length === 1 ? 'heart' : 'hearts'}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {next && (
          <Card onClick={() => onTab('days')} testId="beloved-next-milestone">
            <span className="text-xs font-semibold uppercase tracking-wide text-amber-200/80">Next milestone</span>
            <span className="mt-1 block font-semibold text-white">{next.label}</span>
            <span className="block text-sm text-slate-300">{countdownLabel(next.inDays)} · {dayLabel(next.date)}</span>
          </Card>
        )}
        {nextDay && (
          <Card onClick={() => onTab('days')} testId="beloved-next-day">
            <span className="text-xs font-semibold uppercase tracking-wide text-amber-200/80">Coming up</span>
            <span className="mt-1 block break-words font-semibold text-white">{nextDay.name}</span>
            <span className="block text-sm text-slate-300">{countdownLabel(nextDay.inDays)} · {dayLabel(nextDay.date)}</span>
          </Card>
        )}
      </div>

      <Card onClick={() => onTab('today')} testId="beloved-verse">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-200/80">
          <Sparkles size={13} aria-hidden /> Today's verse
        </span>
        <span className="mt-1 block font-serif text-[15px] leading-relaxed text-rose-50">
          {verse.loading ? 'Loading…' : verse.failed ? 'Open the Bible to read it today.' : verse.text}
        </span>
        <span className="mt-1 block text-xs font-semibold text-rose-200">{content.devotion.ref}</span>
      </Card>

      {latestNote && (
        <Card onClick={() => onTab('notes')} testId="beloved-latest-note">
          <span className="text-xs font-semibold uppercase tracking-wide text-amber-200/80">A note from {latestNote.byName}</span>
          <span className="mt-1 line-clamp-3 block whitespace-pre-wrap break-words text-sm text-slate-100">
            {latestNote.record.b === 'note' ? latestNote.record.text : ''}
          </span>
        </Card>
      )}
    </div>
  );
}

/** The couple's shared details: wedding and first date, birthdays, the cover photo, and this phone's reminders. */
function ProfileSheet({
  data,
  token,
  vault,
  reminders,
  onClose,
}: {
  data: BelovedData;
  token: string;
  vault: PrivateVault;
  reminders: ReminderState | null;
  onClose: () => void;
}) {
  const current = data.view.profile;
  const record = current?.record;
  const [wedding, setWedding] = useState(record?.wedding ?? '');
  const [firstDate, setFirstDate] = useState(record?.firstDate ?? '');
  const [title, setTitle] = useState(record?.title ?? '');
  const [birthdays, setBirthdays] = useState<Record<string, string>>(record?.birthdays ?? {});
  const [cover, setCover] = useState(record?.cover);
  const [details, setDetails] = useState(reminderDetails());
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const pickCover = async (file: File) => {
    if (!IMAGE_TYPES.has(file.type)) {
      toast.error('Choose a photo');
      return;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      toast.error('Photos are limited to 25 MB');
      return;
    }
    setBusy(true);
    try {
      const plain = new Uint8Array(await file.arrayBuffer());
      let sealed: Uint8Array<ArrayBuffer>;
      try {
        sealed = await vault.encrypt(plain);
      } finally {
        plain.fill(0);
      }
      const { id } = await api.uploadPrivateBlob(token, sealed);
      setCover({ blob: id, mime: file.type });
    } catch (error) {
      toast.error(errorText(error, 'Could not add the photo'));
    } finally {
      setBusy(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const next: ProfileRecord = { b: 'profile' };
      if (parseDay(wedding)) next.wedding = wedding;
      if (parseDay(firstDate)) next.firstDate = firstDate;
      if (title.trim()) next.title = title.trim().slice(0, 120);
      const days = Object.fromEntries(Object.entries(birthdays).filter(([, v]) => parseDay(v)));
      if (Object.keys(days).length) next.birthdays = days;
      if (cover) next.cover = cover;
      await data.save('profile', next, current ? { id: current.id } : {});
      // The old cover's ciphertext is no longer needed by anyone.
      if (record?.cover && record.cover.blob !== cover?.blob) void api.deletePrivateBlob(token, record.cover.blob).catch(() => {});
      setReminderDetails(details);
      onClose();
    } catch (error) {
      toast.error(errorText(error, 'Could not save'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[90] flex items-end justify-center bg-black/60 sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-label="Our details" data-testid="beloved-settings">
      <form
        onSubmit={(event) => void submit(event)}
        className="flex max-h-[92dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-3xl border border-rose-300/15 bg-slate-900 sm:rounded-3xl"
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-white/5 px-3 py-2">
          <h3 className="min-w-0 flex-1 truncate font-serif text-base font-semibold text-rose-100">Our details</h3>
          <button type="button" onClick={onClose} aria-label="Close" className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-300 hover:bg-white/10">
            <X size={18} />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          <p className="text-xs text-slate-400">Only the two of you can read these. They are encrypted on this phone before they are saved.</p>
          <Field label="Wedding date">
            <input type="date" value={wedding} onChange={(e) => setWedding(e.target.value)} data-testid="beloved-wedding" className="glass-input min-h-11 w-full text-base sm:text-sm" />
          </Field>
          <Field label="Our first date">
            <input type="date" value={firstDate} onChange={(e) => setFirstDate(e.target.value)} data-testid="beloved-first-date" className="glass-input min-h-11 w-full text-base sm:text-sm" />
          </Field>
          {data.couple.map((person) => (
            <Field key={person.username} label={`${person.display_name}'s birthday`}>
              <input
                type="date"
                value={birthdays[person.username] ?? ''}
                onChange={(e) => setBirthdays((b) => ({ ...b, [person.username]: e.target.value }))}
                data-testid={`beloved-birthday-${person.username}`}
                className="glass-input min-h-11 w-full text-base sm:text-sm"
              />
            </Field>
          ))}
          <Field label="What we call ourselves (optional)">
            <input type="text" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} placeholder="The two of us" data-testid="beloved-title" className="glass-input min-h-11 w-full text-base sm:text-sm" />
          </Field>
          <div>
            <span className="mb-1 block text-xs font-semibold text-slate-300">Cover photo</span>
            <input
              ref={fileRef}
              type="file"
              accept={[...IMAGE_TYPES].join(',')}
              className="hidden"
              data-testid="beloved-cover-input"
              onChange={(event) => {
                pickerDone(token);
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void pickCover(file);
              }}
            />
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  pickerOpening(token);
                  fileRef.current?.click();
                }}
                disabled={busy}
                className="flex min-h-11 items-center gap-1.5 rounded-xl bg-white/[0.07] px-3 text-sm font-semibold text-slate-100 disabled:opacity-50"
              >
                <ImagePlus size={16} aria-hidden /> {cover ? 'Change photo' : 'Choose a photo'}
              </button>
              {cover && (
                <button type="button" onClick={() => setCover(undefined)} className="flex min-h-11 items-center gap-1.5 rounded-xl px-3 text-sm text-rose-200 hover:bg-white/5">
                  <Trash2 size={15} aria-hidden /> Remove
                </button>
              )}
            </div>
            {cover && <p className="mt-1 text-xs text-emerald-200" data-testid="beloved-cover-ready">Photo encrypted and ready.</p>}
          </div>
          <label className="flex min-h-11 items-start gap-3 rounded-xl border border-white/10 p-3 text-sm text-slate-200">
            <input type="checkbox" checked={details} onChange={(e) => setDetails(e.target.checked)} className="mt-0.5 h-5 w-5 shrink-0" data-testid="beloved-reminder-details" />
            <span>
              Show what the day is in reminders on this phone
              <span className="mt-0.5 block text-xs text-slate-400">
                Off: reminders only say something special is coming. On: they name the day, but never on the lock screen.
              </span>
            </span>
          </label>
          <p className="text-xs text-slate-400" data-testid="beloved-reminder-state">
            {reminders === 'on'
              ? 'Reminders for your special days are set on this phone.'
              : reminders === 'unsupported'
                ? 'Update the Jarvis app to get reminders for your special days.'
                : 'Reminders for your special days come on the Jarvis Android app.'}
          </p>
        </div>
        <div className="shrink-0 border-t border-white/5 p-3">
          <button type="submit" disabled={busy} data-testid="beloved-settings-save"
            className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-rose-600 text-sm font-semibold text-white disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />} Save
          </button>
        </div>
      </form>
    </div>
  );
}
