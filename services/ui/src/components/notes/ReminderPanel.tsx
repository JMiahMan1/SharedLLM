import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bell, BellOff, Loader2, MapPin } from 'lucide-react';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { geoZonesQueryKey, houseConfigQueryOptions, zonePlaces } from '../../lib/wanderQueries';
import { placeReminderText, type NotePlaceReminder, type NoteReminder } from '../../lib/notesMeta';

export interface Place {
  zone: string;
  name: string;
}

/** The family's places (Home Assistant zones, as Wander reads them), by name. */
function placesFrom(data: unknown): Place[] {
  return zonePlaces(data)
    .filter((p) => p.id.startsWith('zone.'))
    .map((p) => ({ zone: p.id, name: p.name }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Run one change, with its busy flag and error message (both parts of the panel). */
function useChange(fallback: string) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      setError(errorText(err, fallback));
    } finally {
      setBusy(false);
    }
  };
  return { error, setError, busy, run };
}

/**
 * "When I get home": a reminder on one of the family's places, fired by this
 * person's own arrival or departure (geo watches the location the phone
 * already shares). Only one per note; setting another replaces it.
 */
function PlaceReminderSection({
  reminder,
  waiting,
  onSet,
  onClear,
}: {
  reminder?: NotePlaceReminder;
  /** Whether geo still has it: false once it went off, undefined while unknown. */
  waiting?: boolean;
  onSet: (place: Place, on: 'arrive' | 'leave') => Promise<void>;
  onClear: () => Promise<void>;
}) {
  const zonesQuery = useQuery({
    queryKey: geoZonesQueryKey(),
    queryFn: () => api.getGeoZones(),
    ...houseConfigQueryOptions,
  });
  const places = placesFrom(zonesQuery.data);
  const [zone, setZone] = useState(reminder?.zone ?? '');
  const [on, setOn] = useState<'arrive' | 'leave'>(reminder?.on ?? 'arrive');
  const { error, busy, run } = useChange('Could not change the place reminder');
  const chosen = places.find((p) => p.zone === zone) ?? (zone ? undefined : places[0]);

  return (
    <div className="space-y-2 border-t border-white/5 pt-2" data-testid="note-place-reminder">
      <p className="flex items-center gap-2 text-xs text-slate-200">
        <MapPin size={13} />
        {reminder
          ? waiting === false
            ? `Place reminder went off (${placeReminderText(reminder).toLowerCase()})`
            : `Reminds you when ${placeReminderText(reminder).toLowerCase()}`
          : 'No place reminder'}
      </p>
      {zonesQuery.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-slate-400" role="status">
          <Loader2 size={13} className="animate-spin" /> Loading the family’s places…
        </p>
      ) : zonesQuery.isError ? (
        <p className="text-xs text-rose-300" role="alert">
          {errorText(zonesQuery.error, 'Could not load the family’s places')}
        </p>
      ) : places.length === 0 ? (
        <p className="text-xs text-slate-400">No places yet. Places are the zones set up in Home Assistant (Wander lists them).</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="Reminder place"
            value={chosen?.zone ?? ''}
            onChange={(e) => setZone(e.target.value)}
            className="glass-input min-h-9 px-2 py-1 text-base sm:text-xs"
          >
            {!chosen && <option value="">Pick a place</option>}
            {places.map((p) => (
              <option key={p.zone} value={p.zone}>
                {p.name}
              </option>
            ))}
          </select>
          <select
            aria-label="Remind me when I"
            value={on}
            onChange={(e) => setOn(e.target.value === 'leave' ? 'leave' : 'arrive')}
            className="glass-input min-h-9 px-2 py-1 text-base sm:text-xs"
          >
            <option value="arrive">arrive</option>
            <option value="leave">leave</option>
          </select>
          <button
            type="button"
            disabled={busy || !chosen}
            className="glass-button min-h-9 px-3 py-1 text-xs"
            onClick={() => chosen && void run(() => onSet(chosen, on))}
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <MapPin size={13} />}{' '}
            {reminder ? 'Change place reminder' : 'Set place reminder'}
          </button>
          {reminder && (
            <button
              type="button"
              disabled={busy}
              className="min-h-9 rounded-xl px-3 py-1 text-xs text-rose-300 hover:bg-white/5"
              onClick={() => void run(onClear)}
            >
              <BellOff size={13} className="mr-1 inline" /> Remove place reminder
            </button>
          )}
        </div>
      )}
      {error && (
        <p className="text-xs text-rose-300" role="alert">
          {error}
        </p>
      )}
      <p className="text-[11px] text-slate-500">
        Goes off on your next arrival or departure, from the location your phone shares with the family (Location set
        to “Allow all the time”). Only you get it.
      </p>
    </div>
  );
}

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
  place,
}: {
  reminder?: NoteReminder;
  onSet: (at: Date) => Promise<void>;
  onClear: () => Promise<void>;
  /** The note's place reminder, when places are offered. */
  place?: {
    reminder?: NotePlaceReminder;
    waiting?: boolean;
    onSet: (place: Place, on: 'arrive' | 'leave') => Promise<void>;
    onClear: () => Promise<void>;
  };
}) {
  const [now] = useState(() => new Date());
  const [value, setValue] = useState(() =>
    localInputValue(reminder && Date.parse(reminder.at) > now.getTime() ? new Date(reminder.at) : quickPicks(now)[0].at),
  );
  const { error, setError, busy, run } = useChange('Could not change the reminder');
  const due = reminder ? new Date(reminder.at) : null;
  const passed = Boolean(due && due.getTime() <= now.getTime());

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
      {place && <PlaceReminderSection {...place} />}
    </section>
  );
}
