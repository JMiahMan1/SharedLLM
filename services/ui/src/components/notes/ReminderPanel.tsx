import { useState } from 'react';
import { Bell, BellOff, Loader2 } from 'lucide-react';
import type { NoteReminder } from '../../lib/notesMeta';

/** `YYYY-MM-DDTHH:mm` in local time, the value a datetime-local field takes. */
function localInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * Still in the future at the moment of the tap. Checked here because the
 * timers read a past clock time as that time tomorrow, which is not what a
 * reminder on a note means.
 */
function isAhead(at: Date): boolean {
  return at.getTime() > Date.now();
}

/** Keep's quick picks: this evening (while it is still ahead) and tomorrow morning. */
function quickPicks(now: Date): Array<{ label: string; at: Date }> {
  const evening = new Date(now);
  evening.setHours(18, 0, 0, 0);
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  tomorrow.setHours(9, 0, 0, 0);
  const picks = [{ label: 'Tomorrow 9:00', at: tomorrow }];
  if (evening.getTime() - now.getTime() > 15 * 60_000) picks.unshift({ label: 'This evening 18:00', at: evening });
  return picks;
}

/**
 * A note's timed reminder. Setting one schedules a personal reminder on the
 * existing timers (it rings like any other reminder and its notice opens the
 * note); the shared notes index remembers when it fires.
 */
export default function ReminderPanel({
  reminder,
  onSet,
  onClear,
}: {
  reminder?: NoteReminder;
  onSet: (at: Date) => Promise<void>;
  onClear: () => Promise<void>;
}) {
  const [now] = useState(() => new Date());
  const [value, setValue] = useState(() =>
    localInputValue(reminder && Date.parse(reminder.at) > now.getTime() ? new Date(reminder.at) : quickPicks(now)[0].at),
  );
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const due = reminder ? new Date(reminder.at) : null;
  const passed = Boolean(due && due.getTime() <= now.getTime());

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change the reminder');
    } finally {
      setBusy(false);
    }
  };

  const set = (at: Date) => {
    if (Number.isNaN(at.getTime())) {
      setError('Pick a date and time.');
      return;
    }
    if (!isAhead(at)) {
      setError('Pick a time that is still ahead.');
      return;
    }
    void run(() => onSet(at));
  };

  return (
    <section className="space-y-2 rounded-xl border border-white/10 p-3" aria-label="Reminder" data-testid="note-reminder">
      <p className="flex items-center gap-2 text-xs text-slate-200">
        <Bell size={13} />
        {due
          ? passed
            ? `Reminder went off ${due.toLocaleString()}`
            : `Reminds you ${due.toLocaleString()}`
          : 'No reminder yet'}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {quickPicks(now).map((pick) => (
          <button
            key={pick.label}
            type="button"
            disabled={busy}
            className="glass-button min-h-9 px-3 py-1 text-xs"
            onClick={() => set(pick.at)}
          >
            {pick.label}
          </button>
        ))}
        <input
          type="datetime-local"
          aria-label="Reminder time"
          value={value}
          min={localInputValue(now)}
          onChange={(e) => setValue(e.target.value)}
          className="glass-input min-h-9 px-2 py-1 text-base sm:text-xs"
        />
        <button
          type="button"
          disabled={busy}
          className="glass-button min-h-9 px-3 py-1 text-xs"
          onClick={() => set(new Date(value))}
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Bell size={13} />} {reminder ? 'Change reminder' : 'Set reminder'}
        </button>
        {reminder && (
          <button
            type="button"
            disabled={busy}
            className="min-h-9 rounded-xl px-3 py-1 text-xs text-rose-300 hover:bg-white/5"
            onClick={() => void run(onClear)}
          >
            <BellOff size={13} className="mr-1 inline" /> Remove
          </button>
        )}
      </div>
      {error && (
        <p className="text-xs text-rose-300" role="alert">
          {error}
        </p>
      )}
      <p className="text-[11px] text-slate-500">
        A personal reminder: it rings for you like your other reminders, and tapping it opens this note.
      </p>
    </section>
  );
}
