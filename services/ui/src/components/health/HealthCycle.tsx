import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CalendarDays,
  Droplets,
  Loader2,
  Lock,
  Plus,
  Save,
  Share2,
  Trash2,
  X,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { formatDayLabel, localDay } from '../../lib/healthHub';
import type { CycleFlow, HealthCycleLog, HealthCycleState } from '../../types/api';

const FLOW_OPTIONS: Array<[CycleFlow, string]> = [
  ['spotting', 'Spotting'],
  ['light', 'Light'],
  ['medium', 'Medium'],
  ['heavy', 'Heavy'],
];

const SYMPTOM_OPTIONS: Array<[string, string]> = [
  ['cramps', 'Cramps'],
  ['headache', 'Headache'],
  ['bloating', 'Bloating'],
  ['acne', 'Acne'],
  ['breast_tenderness', 'Breast tenderness'],
  ['fatigue', 'Fatigue'],
  ['cravings', 'Cravings'],
  ['backache', 'Backache'],
  ['nausea', 'Nausea'],
  ['insomnia', 'Poor sleep'],
  ['mood_swing', 'Mood changes'],
];

const PHASE_COPY: Record<string, string> = {
  menstrual: 'Period',
  follicular: 'Follicular',
  ovulation: 'Estimated ovulation',
  luteal: 'Luteal',
  late: 'Period expected',
  unknown: 'Getting to know your cycle',
};

function nextPeriodLabel(state: HealthCycleState['summary']): string | null {
  if (!state || state.days_until_next_period === null) return null;
  const days = state.days_until_next_period;
  if (days > 1) return `in ${days} days`;
  if (days === 1) return 'tomorrow';
  if (days === 0) return 'today';
  const late = Math.abs(days);
  return `about ${late} day${late === 1 ? '' : 's'} late`;
}

function LogEditor({
  day,
  existing,
  onSaved,
}: {
  day: string;
  existing: HealthCycleLog | undefined;
  onSaved: () => void;
}) {
  const [symptoms, setSymptoms] = useState<string[]>(existing?.symptoms ?? []);
  const [mood, setMood] = useState(existing?.mood ? String(existing.mood) : '');
  const [energy, setEnergy] = useState(existing?.energy ? String(existing.energy) : '');
  const [bbt, setBbt] = useState(existing?.bbt_c ? String(existing.bbt_c) : '');
  const [ovulation, setOvulation] = useState(existing?.ovulation_test ?? '');
  const [note, setNote] = useState(existing?.note ?? '');

  const save = useMutation({
    mutationFn: () =>
      api.saveHealthCycleLog(day, {
        symptoms,
        mood: mood ? Number(mood) : null,
        energy: energy ? Number(energy) : null,
        bbt_c: bbt ? Number(bbt) : null,
        ovulation_test: ovulation
          ? (ovulation as 'negative' | 'positive' | 'inconclusive')
          : null,
        note: note.trim(),
      }),
    onSuccess: () => {
      toast.success(`Saved ${formatDayLabel(day)}`);
      onSaved();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not save the log'),
  });

  const remove = useMutation({
    mutationFn: () => api.deleteHealthCycleLog(day),
    onSuccess: () => {
      toast.success('Log removed');
      onSaved();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not remove the log'),
  });

  return (
    <div className="mt-3 space-y-3">
      <div>
        <p className="text-[10px] uppercase tracking-wider text-slate-500">How does today feel?</p>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {SYMPTOM_OPTIONS.map(([value, label]) => {
            const active = symptoms.includes(value);
            return (
              <button
                key={value}
                type="button"
                aria-pressed={active}
                onClick={() =>
                  setSymptoms((current) =>
                    active ? current.filter((item) => item !== value) : [...current, value],
                  )
                }
                className={`rounded-full border px-2.5 py-1 text-[10px] font-medium transition-colors pointer-coarse:py-1.5 ${
                  active
                    ? 'border-purple-500/40 bg-purple-500/20 text-purple-200'
                    : 'border-white/10 bg-white/[0.03] text-slate-400 hover:text-white'
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <label className="text-[10px] text-slate-500">
          Mood
          <select
            value={mood}
            onChange={(event) => setMood(event.target.value)}
            className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
          >
            <option value="">-</option>
            {[1, 2, 3, 4, 5].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[10px] text-slate-500">
          Energy
          <select
            value={energy}
            onChange={(event) => setEnergy(event.target.value)}
            className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
          >
            <option value="">-</option>
            {[1, 2, 3, 4, 5].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[10px] text-slate-500">
          Temp °C
          <input
            type="number"
            step="0.01"
            inputMode="decimal"
            value={bbt}
            onChange={(event) => setBbt(event.target.value)}
            placeholder="optional"
            className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
          />
        </label>
        <label className="text-[10px] text-slate-500">
          Ovulation test
          <select
            value={ovulation}
            onChange={(event) => setOvulation(event.target.value)}
            className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
          >
            <option value="">-</option>
            <option value="negative">Negative</option>
            <option value="positive">Positive</option>
            <option value="inconclusive">Inconclusive</option>
          </select>
        </label>
      </div>
      <label className="block text-[10px] text-slate-500">
        Note
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={500}
          placeholder="Anything worth remembering"
          className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
        />
      </label>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="glass-button inline-flex min-h-9 items-center gap-1.5 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-10"
        >
          {save.isPending ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
          Save day
        </button>
        {existing && (
          <button
            type="button"
            onClick={() => remove.mutate()}
            disabled={remove.isPending}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-xs text-slate-500 hover:text-red-300 disabled:opacity-50"
          >
            <Trash2 size={13} /> Remove
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Cycle tracking, opt-in and owner-only by default.
 *
 * Rendered only when the health profile is not explicitly male. The estimate
 * is a calendar aid: the copy never says fertile window, the disclaimer is
 * always present, and a minor's sharing is limited to one designated mother
 * or trusted adult by the service.
 */
export default function HealthCycle() {
  const queryClient = useQueryClient();
  const today = useMemo(() => localDay(), []);
  const [selectedDay, setSelectedDay] = useState(today);
  const [startDay, setStartDay] = useState(today);
  const [endDay, setEndDay] = useState('');
  const [flow, setFlow] = useState<CycleFlow>('medium');
  const [periodNote, setPeriodNote] = useState('');
  const [avgCycle, setAvgCycle] = useState('');
  const [avgPeriod, setAvgPeriod] = useState('');
  const [guardian, setGuardian] = useState('');
  const [shareViewer, setShareViewer] = useState('');

  const cycleQuery = useQuery({
    queryKey: ['health-cycle', today],
    queryFn: () => api.getHealthCycle(today),
    staleTime: 60_000,
  });
  const familyQuery = useQuery({
    queryKey: ['family-directory'],
    queryFn: () => api.getFamilyDirectory(),
    staleTime: 300_000,
  });
  const sharesQuery = useQuery({
    queryKey: ['health-shares'],
    queryFn: () => api.getHealthShares(),
  });

  const data = cycleQuery.data;
  const currentLog = data?.logs.find((entry) => entry.day === selectedDay);
  const cycleShares = (sharesQuery.data?.shares ?? []).filter((share) => share.scope === 'cycle');

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['health-cycle'] });
    void queryClient.invalidateQueries({ queryKey: ['health-shares'] });
    void queryClient.invalidateQueries({ queryKey: ['health-summary'] });
    void queryClient.invalidateQueries({ queryKey: ['health-family'] });
  };

  const settings = useMutation({
    mutationFn: (body: Parameters<typeof api.updateHealthCycleSettings>[0]) =>
      api.updateHealthCycleSettings(body),
    onSuccess: () => {
      toast.success('Cycle settings saved');
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not save cycle settings'),
  });

  const addPeriod = useMutation({
    mutationFn: () =>
      api.addHealthCyclePeriod({
        start_day: startDay,
        end_day: endDay || null,
        flow,
        note: periodNote.trim(),
      }),
    onSuccess: () => {
      toast.success('Period logged');
      setEndDay('');
      setPeriodNote('');
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not log the period'),
  });

  const removePeriod = useMutation({
    mutationFn: (periodId: number) => api.deleteHealthCyclePeriod(periodId),
    onSuccess: () => {
      toast.success('Period removed');
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not remove the period'),
  });

  const share = useMutation({
    mutationFn: (body: { viewer: string; enabled: boolean }) =>
      api.updateHealthShare({ viewer: body.viewer, scope: 'cycle', enabled: body.enabled }),
    onSuccess: () => {
      toast.success('Cycle sharing updated');
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not update sharing'),
  });

  if (cycleQuery.isLoading) {
    return (
      <section className="glass-panel flex items-center gap-2 rounded-2xl border border-white/5 p-4 text-xs text-slate-400">
        <Loader2 size={14} className="animate-spin" /> Loading cycle settings…
      </section>
    );
  }
  if (!data || !data.applicable) return null;

  if (!data.enabled) {
    return (
      <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="cycle-heading">
        <h3 id="cycle-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          <Droplets size={13} className="text-rose-300" />
          Cycle tracking
        </h3>
        <p className="mt-2 text-xs text-slate-400">
          Off by default and private. Log period dates and symptoms to see an estimated phase and
          next period. This is a calendar estimate.
        </p>
        {data.is_minor && (
          <p className="mt-1.5 text-[11px] text-slate-500">
            Nothing is shared unless you choose it. As a teen you can share with one mother or
            trusted adult after you set them below.
          </p>
        )}
        <button
          type="button"
          onClick={() => settings.mutate({ enabled: true })}
          disabled={settings.isPending}
          className="glass-button mt-3 inline-flex min-h-10 items-center gap-1.5 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-11"
        >
          {settings.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
          Turn on cycle tracking
        </button>
      </section>
    );
  }

  const summary = data.summary;
  const nextLabel = nextPeriodLabel(summary);

  return (
    <section className="glass-panel space-y-4 rounded-2xl border border-white/5 p-4" aria-labelledby="cycle-heading">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 id="cycle-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            <Droplets size={13} className="text-rose-300" />
            Cycle tracking
          </h3>
          <p className="mt-1 flex items-center gap-1 text-[10px] text-slate-500">
            <Lock size={10} /> Private. Only you, plus anyone you explicitly share with.
          </p>
        </div>
        <button
          type="button"
          onClick={() => settings.mutate({ enabled: false })}
          disabled={settings.isPending}
          className="rounded-lg px-2 py-1 text-[10px] text-slate-500 hover:text-red-300 disabled:opacity-50"
        >
          Turn off
        </button>
      </div>

      {summary && (
        <div className="rounded-xl border border-rose-500/15 bg-rose-500/[0.06] p-3">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p className="text-lg font-semibold text-white">
              {PHASE_COPY[summary.phase] ?? summary.phase_label}
            </p>
            {summary.cycle_day !== null && (
              <p className="text-xs text-slate-400">Cycle day {summary.cycle_day}</p>
            )}
            {nextLabel && <p className="text-xs text-slate-400">Next period {nextLabel}</p>}
          </div>
          <p className="mt-1.5 text-[11px] text-slate-400">{summary.detail}</p>
          {summary.irregular && (
            <p className="mt-1 text-[10px] text-amber-300/90">
              Your logged cycles vary widely; treat dates as a loose estimate.
            </p>
          )}
          <p className="mt-2 text-[10px] text-slate-500">
            {summary.disclaimer}
            {summary.confidence !== 'high' && ' More logged periods sharpen the estimate.'}
          </p>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
          <h4 className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-300">
            <CalendarDays size={13} className="text-rose-300" /> Log a period
          </h4>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <label className="text-[10px] text-slate-500">
              Start
              <input
                type="date"
                value={startDay}
                max={today}
                onChange={(event) => setStartDay(event.target.value)}
                className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
              />
            </label>
            <label className="text-[10px] text-slate-500">
              End (optional)
              <input
                type="date"
                value={endDay}
                min={startDay}
                max={today}
                onChange={(event) => setEndDay(event.target.value)}
                className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
              />
            </label>
          </div>
          <div className="mt-2 flex items-end gap-2">
            <label className="flex-1 text-[10px] text-slate-500">
              Flow
              <select
                value={flow}
                onChange={(event) => setFlow(event.target.value as CycleFlow)}
                className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
              >
                {FLOW_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => addPeriod.mutate()}
              disabled={addPeriod.isPending || !startDay}
              className="glass-button inline-flex min-h-9 items-center gap-1.5 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-10"
            >
              {addPeriod.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
              Log
            </button>
          </div>
          <input
            value={periodNote}
            onChange={(event) => setPeriodNote(event.target.value)}
            maxLength={200}
            placeholder="Note (optional)"
            className="glass-input mt-2 h-9 w-full px-2 text-xs pointer-coarse:h-10"
          />

          {data.periods.length > 0 && (
            <ul className="mt-3 space-y-1">
              {data.periods.slice(0, 12).map((period) => (
                <li
                  key={period.id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-2 py-1.5 text-[11px] text-slate-300"
                >
                  <span>
                    {formatDayLabel(period.start_day)}
                    {period.end_day ? ` - ${formatDayLabel(period.end_day)}` : ''}
                    <span className="ml-1.5 capitalize text-slate-500">{period.flow}</span>
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove period starting ${period.start_day}`}
                    onClick={() => removePeriod.mutate(period.id)}
                    disabled={removePeriod.isPending}
                    className="text-slate-500 hover:text-red-300 disabled:opacity-50"
                  >
                    <X size={12} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-[11px] font-semibold text-slate-300">Daily symptoms</h4>
            <input
              type="date"
              value={selectedDay}
              max={today}
              onChange={(event) => setSelectedDay(event.target.value)}
              className="glass-input h-8 px-2 text-[11px] pointer-coarse:h-9"
            />
          </div>
          <LogEditor
            key={`${selectedDay}-${currentLog?.updated_at ?? 'new'}`}
            day={selectedDay}
            existing={currentLog}
            onSaved={invalidate}
          />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
          <h4 className="text-[11px] font-semibold text-slate-300">Cycle settings</h4>
          <p className="mt-1 text-[10px] text-slate-500">
            Optional. Without these the estimate learns from your logged periods in 15-90 day
            range.
          </p>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <label className="text-[10px] text-slate-500">
              Average cycle (days)
              <input
                type="number"
                min={15}
                max={90}
                value={avgCycle || (data.average_cycle_length ? String(data.average_cycle_length) : '')}
                onChange={(event) => setAvgCycle(event.target.value)}
                placeholder="learned"
                className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
              />
            </label>
            <label className="text-[10px] text-slate-500">
              Average period (days)
              <input
                type="number"
                min={1}
                max={20}
                value={avgPeriod || (data.average_period_length ? String(data.average_period_length) : '')}
                onChange={(event) => setAvgPeriod(event.target.value)}
                placeholder="learned"
                className="glass-input mt-1 h-9 w-full px-2 text-xs pointer-coarse:h-10"
              />
            </label>
          </div>
          <button
            type="button"
            onClick={() =>
              settings.mutate({
                average_cycle_length: avgCycle ? Number(avgCycle) : null,
                average_period_length: avgPeriod ? Number(avgPeriod) : null,
              })
            }
            disabled={settings.isPending}
            className="glass-button mt-2 inline-flex min-h-9 items-center gap-1.5 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-10"
          >
            <Save size={13} /> Save settings
          </button>

          {data.is_minor && (
            <div className="mt-3 border-t border-white/5 pt-3">
              <p className="text-[10px] text-slate-500">
                Mother / trusted adult. Only this person can see your cycle data if you share it.
              </p>
              <div className="mt-1.5 flex items-end gap-2">
                <select
                  value={guardian || data.guardian_username || ''}
                  onChange={(event) => setGuardian(event.target.value)}
                  className="glass-input h-9 min-w-32 flex-1 px-2 text-xs capitalize pointer-coarse:h-10"
                >
                  <option value="">Choose…</option>
                  {(familyQuery.data ?? []).map((member) => (
                    <option key={member.username} value={member.username}>
                      {member.display_name || member.username}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => settings.mutate({ guardian_username: guardian || null })}
                  disabled={settings.isPending}
                  className="glass-button min-h-9 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-10"
                >
                  Save
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3">
          <h4 className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-300">
            <Share2 size={13} className="text-sky-300" /> Cycle sharing
          </h4>
          <p className="mt-1 text-[10px] text-slate-500">
            Off by default. A shared person sees only the current phase and next-period estimate,
            never symptoms, flow or notes.
          </p>
          {cycleShares.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {cycleShares.map((entry) => (
                <span
                  key={entry.viewer}
                  className="flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] capitalize text-slate-300"
                >
                  {entry.viewer}
                  <button
                    type="button"
                    aria-label={`Stop sharing cycle data with ${entry.viewer}`}
                    onClick={() => share.mutate({ viewer: entry.viewer, enabled: false })}
                    className="text-slate-500 hover:text-red-300"
                  >
                    <X size={10} />
                  </button>
                </span>
              ))}
            </div>
          )}
          {data.is_minor ? (
            data.guardian_username ? (
              <p className="mt-2 text-[11px] text-slate-400">
                You can share with {data.guardian_username} once you save them above.
              </p>
            ) : (
              <p className="mt-2 text-[11px] text-amber-300/90">
                Choose a mother or trusted adult above to enable sharing.
              </p>
            )
          ) : (
            <div className="mt-2 flex items-end gap-2">
              <select
                value={shareViewer}
                onChange={(event) => setShareViewer(event.target.value)}
                className="glass-input h-9 min-w-32 flex-1 px-2 text-xs capitalize pointer-coarse:h-10"
              >
                <option value="">Choose a person…</option>
                {(familyQuery.data ?? []).map((member) => (
                  <option key={member.username} value={member.username}>
                    {member.display_name || member.username}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => share.mutate({ viewer: shareViewer, enabled: true })}
                disabled={share.isPending || !shareViewer}
                className="glass-button min-h-9 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-10"
              >
                Share
              </button>
            </div>
          )}
          {data.is_minor && data.guardian_username && (
            <button
              type="button"
              onClick={() => share.mutate({ viewer: data.guardian_username as string, enabled: true })}
              disabled={share.isPending}
              className="glass-button mt-2 min-h-9 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-10"
            >
              Share with {data.guardian_username}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
