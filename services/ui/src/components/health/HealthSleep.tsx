import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Loader2, Moon, Pencil, Smartphone, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import {
  formatDayLabel,
  formatMinutes,
  localDay,
  scoreTone,
  sleepWindowLabel,
} from '../../lib/healthHub';
import type { SleepEstimate, SleepSession } from '../../types/api';

const QUALITY_WORDS = ['', 'Rough', 'Poor', 'Okay', 'Good', 'Great'];
const ESTIMATE_SOURCE = 'usage_estimate';

/** `HH:MM` on this device's clock for an ISO time, for the time inputs. */
function clockTime(iso: string | null): string {
  if (!iso) return '';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
}

function sourceLabel(source: string, long = false): string {
  if (source === 'manual') return long ? 'Logged by hand' : 'hand logged';
  if (source === ESTIMATE_SOURCE) return long ? 'Estimated from device use' : 'estimated from device use';
  return long ? `Imported from ${source}` : source;
}

/**
 * Sleep: last night interpreted, a hand-log form, and the recent nights.
 *
 * The form accepts either a bedtime and wake time or a plain duration, because
 * a person who only remembers "about seven hours" should not be locked out of
 * their own history. The server computes the duration when times are given, so
 * the number shown is never a client-side guess.
 *
 * "Estimate from my devices" fills the form from when the phones and tablets
 * assigned to the person were in use; it is only a starting point to correct
 * before saving. Saved untouched it is kept as an estimate (a hand log or a
 * wearable outranks it); any saved night can be edited later.
 */
export default function HealthSleep() {
  const queryClient = useQueryClient();
  const [night, setNight] = useState(localDay());
  const [bedtime, setBedtime] = useState('');
  const [wakeTime, setWakeTime] = useState('');
  const [duration, setDuration] = useState('');
  const [quality, setQuality] = useState<number | null>(null);
  const [efficiency, setEfficiency] = useState('');
  const [note, setNote] = useState('');
  // The estimate the form was filled from, and the saved night being edited.
  const [estimate, setEstimate] = useState<SleepEstimate | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);

  const historyQuery = useQuery({
    queryKey: ['health-sleep', 14],
    queryFn: () => api.getSleep(14),
    staleTime: 60_000,
  });
  const summaryQuery = useQuery({
    queryKey: ['health-summary', night],
    queryFn: () => api.getHealthSummary(night),
    staleTime: 60_000,
  });

  const summary = summaryQuery.data;
  const lastNight = summary?.sleep.last_night ?? null;
  const score = summary?.sleep.score ?? null;
  const tone = scoreTone(score?.score);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['health-sleep'] });
    void queryClient.invalidateQueries({ queryKey: ['health-summary'] });
  };

  const resetForm = () => {
    setBedtime('');
    setWakeTime('');
    setDuration('');
    setQuality(null);
    setEfficiency('');
    setNote('');
    setEstimate(null);
    setEditingId(null);
  };

  const logSleep = useMutation({
    mutationFn: async () => {
      const minutes = duration.trim() ? Number(duration) : null;
      if (!minutes && (!bedtime || !wakeTime)) {
        throw new Error('Add a bedtime and wake time, or a duration');
      }
      // Untouched, the estimate keeps its own minutes (wake-ups taken out) and
      // stays marked as an estimate; any change makes the night the person's.
      const untouched =
        estimate !== null &&
        !duration.trim() &&
        bedtime === clockTime(estimate.bedtime) &&
        wakeTime === clockTime(estimate.wake_time);
      const body = {
        night,
        bedtime: bedtime || null,
        wake_time: wakeTime || null,
        duration_minutes: untouched
          ? estimate.duration_minutes
          : minutes && Number.isFinite(minutes)
            ? Math.round(minutes)
            : null,
        quality,
        efficiency: efficiency.trim() ? Number(efficiency) : null,
        note,
        source: untouched ? ESTIMATE_SOURCE : 'manual',
      };
      if (editingId !== null) {
        return { ...(await api.updateSleep(editingId, body)), duplicate: false };
      }
      return api.logSleep(body);
    },
    onSuccess: (saved) => {
      toast.success(saved.duplicate ? 'That night was already logged' : editingId !== null ? 'Night updated' : 'Sleep saved');
      resetForm();
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not save sleep'),
  });

  const estimateSleep = useMutation({
    mutationFn: () => api.getSleepEstimate(night),
    onSuccess: ({ estimate: found, devices }) => {
      if (!found) {
        toast(
          devices
            ? 'Not enough device use recorded for that night to estimate it.'
            : 'No phone or tablet is assigned to you yet.',
        );
        return;
      }
      setBedtime(clockTime(found.bedtime));
      setWakeTime(clockTime(found.wake_time));
      setDuration('');
      setEstimate(found);
    },
    onError: (error: Error) => toast.error(error.message || 'Could not estimate that night'),
  });

  const editNight = (session: SleepSession) => {
    setEditingId(session.id);
    setEstimate(null);
    setNight(session.night);
    const bed = clockTime(session.bedtime);
    const wake = clockTime(session.wake_time);
    setBedtime(bed);
    setWakeTime(wake);
    // With both times the server works the minutes out from them, so a fixed
    // time is never overridden by a stale duration.
    setDuration(bed && wake ? '' : String(session.duration_minutes));
    setQuality(session.quality);
    setEfficiency(session.efficiency !== null ? String(session.efficiency) : '');
    setNote(session.note || '');
  };

  const removeSleep = useMutation({
    mutationFn: (id: number) => api.deleteSleep(id),
    onSuccess: () => {
      toast.success('Sleep entry removed');
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not remove that entry'),
  });

  return (
    <div className="space-y-3 sm:space-y-4" data-testid="health-sleep">
      <section
        className={`glass-panel rounded-2xl border p-4 sm:p-5 ${tone.border}`}
        aria-labelledby="sleep-score-heading"
      >
        <div className="flex items-center justify-between gap-3">
          <h2
            id="sleep-score-heading"
            className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
          >
            <Moon size={13} className="text-sky-300" />
            Last night
          </h2>
          <span className="text-[10px] text-slate-500">
            {summary?.sleep.need_minutes
              ? `need ${formatMinutes(summary.sleep.need_minutes)}`
              : ''}
          </span>
        </div>

        {summaryQuery.isLoading ? (
          <div className="mt-3 h-20 skeleton rounded-xl" aria-hidden />
        ) : lastNight ? (
          <div className="mt-3 flex items-center gap-4">
            <div
              className={`flex h-20 w-20 shrink-0 flex-col items-center justify-center rounded-full border ${tone.border} ${tone.bg}`}
            >
              <span className={`text-2xl font-bold tabular-nums ${tone.text}`}>
                {score?.score ?? '-'}
              </span>
              <span className="text-[9px] uppercase tracking-wider text-slate-500">of 100</span>
            </div>
            <div className="min-w-0">
              <p className="text-2xl font-bold text-white">{formatMinutes(lastNight.duration_minutes)}</p>
              <p className="text-xs text-slate-400">
                {sleepWindowLabel(lastNight.bedtime, lastNight.wake_time) ?? 'Times not recorded'}
                {lastNight.quality ? ` · felt ${QUALITY_WORDS[lastNight.quality]}` : ''}
              </p>
              <p className="mt-1 text-[11px] text-slate-500">
                {sourceLabel(lastNight.source, true)}
              </p>
              {score && <p className="mt-1 text-xs text-slate-300">{score.detail}</p>}
              {score && score.missing.length > 0 && (
                <p className="mt-0.5 text-[10px] text-slate-500">
                  Add {score.missing.join(' and ')} for a fuller score.
                </p>
              )}
            </div>
          </div>
        ) : summaryQuery.isError ? (
          <p className="mt-2 flex items-center gap-2 text-xs text-amber-300">
            <AlertCircle size={14} /> Sleep history unavailable.
          </p>
        ) : (
          <p className="mt-2 text-xs text-slate-400">
            Nothing logged for {formatDayLabel(night)}. Use the form below, or connect a device
            later - the store accepts both.
          </p>
        )}
      </section>

      <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="log-sleep-heading">
        <div className="flex items-center justify-between gap-2">
          <h3 id="log-sleep-heading" className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            {editingId !== null ? `Edit ${formatDayLabel(night)}` : 'Log a night'}
          </h3>
          <button
            type="button"
            onClick={() => estimateSleep.mutate()}
            disabled={estimateSleep.isPending}
            data-testid="estimate-sleep"
            className="glass-button flex min-h-11 items-center gap-1.5 px-3 text-[11px] font-semibold disabled:opacity-50"
          >
            {estimateSleep.isPending ? <Loader2 size={12} className="animate-spin" /> : <Smartphone size={12} />}
            Estimate from my devices
          </button>
        </div>
        {estimate && (
          <p className="mt-2 text-[11px] text-sky-300" data-testid="sleep-estimate-note">
            Estimated {formatMinutes(estimate.duration_minutes)} from {estimate.devices} device
            {estimate.devices === 1 ? '' : 's'}
            {estimate.wakeups > 0
              ? `, ${estimate.wakeups} wake-up${estimate.wakeups === 1 ? '' : 's'} (${estimate.awake_minutes} min)`
              : ''}
            . Adjust the times if they are off, then save.
          </p>
        )}
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <label className="col-span-2 text-[11px] text-slate-500 sm:col-span-1">
            Woke on
            <input
              type="date"
              value={night}
              max={localDay()}
              onChange={(event) => setNight(event.target.value)}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </label>
          <label className="text-[11px] text-slate-500">
            Bedtime
            <input
              type="time"
              value={bedtime}
              onChange={(event) => setBedtime(event.target.value)}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </label>
          <label className="text-[11px] text-slate-500">
            Wake
            <input
              type="time"
              value={wakeTime}
              onChange={(event) => setWakeTime(event.target.value)}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </label>
          <label className="text-[11px] text-slate-500">
            Or duration (min)
            <input
              type="number"
              min={1}
              max={1440}
              value={duration}
              onChange={(event) => setDuration(event.target.value)}
              placeholder="450"
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </label>
          <label className="text-[11px] text-slate-500">
            Efficiency % (optional)
            <input
              type="number"
              min={0}
              max={100}
              value={efficiency}
              onChange={(event) => setEfficiency(event.target.value)}
              placeholder="device only"
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </label>
        </div>

        <div className="mt-3 flex items-center justify-between gap-2">
          <span className="text-xs text-slate-400">How did it feel?</span>
          <div className="flex gap-1" role="group" aria-label="Sleep quality">
            {[1, 2, 3, 4, 5].map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={quality === value}
                aria-label={`Sleep quality: ${QUALITY_WORDS[value]}`}
                onClick={() => setQuality(value)}
                className={`h-9 min-w-9 rounded-lg border px-1 text-[11px] font-semibold pointer-coarse:h-11 pointer-coarse:min-w-11 ${
                  quality === value
                    ? 'border-sky-500/40 bg-sky-500/20 text-sky-200'
                    : 'border-white/10 bg-white/[0.03] text-slate-400 hover:text-white'
                }`}
              >
                {value}
              </button>
            ))}
          </div>
        </div>

        <input
          type="text"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Anything worth remembering? (optional)"
          className="glass-input mt-3 h-10 w-full px-3 text-xs pointer-coarse:h-11"
        />

        <button
          type="button"
          onClick={() => logSleep.mutate()}
          disabled={logSleep.isPending}
          data-testid="save-sleep"
          className="glass-button mt-3 flex min-h-11 w-full items-center justify-center gap-2 text-xs font-semibold disabled:opacity-50"
        >
          {logSleep.isPending ? <Loader2 size={13} className="animate-spin" /> : <Moon size={13} />}
          {editingId !== null ? 'Save changes' : 'Save night'}
        </button>
        {editingId !== null && (
          <button
            type="button"
            onClick={resetForm}
            className="mt-2 min-h-11 w-full text-xs text-slate-400 hover:text-white"
          >
            Cancel editing
          </button>
        )}
      </section>

      <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="sleep-history-heading">
        <h3
          id="sleep-history-heading"
          className="text-[11px] font-semibold uppercase tracking-wider text-slate-400"
        >
          Recent nights
        </h3>
        {historyQuery.isLoading ? (
          <div className="mt-2 h-16 skeleton rounded-xl" aria-hidden />
        ) : historyQuery.isError ? (
          <p className="mt-2 text-xs text-amber-300">Could not load sleep history.</p>
        ) : (historyQuery.data?.sessions.length ?? 0) === 0 ? (
          <p className="mt-2 text-xs text-slate-500">No nights logged yet.</p>
        ) : (
          <div className="mt-2 divide-y divide-white/5">
            {historyQuery.data?.sessions.map((session) => (
              <div key={session.id} className="flex items-center gap-3 py-2.5" data-testid={`sleep-row-${session.id}`}>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-slate-200">
                    {formatDayLabel(session.night)}
                    <span className="ml-2 text-slate-500">{formatMinutes(session.duration_minutes)}</span>
                  </p>
                  <p className="text-[10px] text-slate-500">
                    {sleepWindowLabel(session.bedtime, session.wake_time) ?? 'Times not recorded'}
                    {session.quality ? ` · felt ${QUALITY_WORDS[session.quality]}` : ''}
                    {' · '}
                    {sourceLabel(session.source)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => editNight(session)}
                  aria-label={`Edit sleep for ${session.night}`}
                  data-testid={`edit-sleep-${session.id}`}
                  className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-white/5 hover:text-white pointer-coarse:h-11 pointer-coarse:w-11"
                >
                  <Pencil size={14} />
                </button>
                <button
                  type="button"
                  onClick={() => removeSleep.mutate(session.id)}
                  disabled={removeSleep.isPending}
                  aria-label={`Remove sleep for ${session.night}`}
                  className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50 pointer-coarse:h-11 pointer-coarse:w-11"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
