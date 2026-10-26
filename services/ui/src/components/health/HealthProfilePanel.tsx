import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, HeartPulse, LayoutGrid, Loader2, Save, Scale, Share2, UserRound, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { formatDayLabel, localDay } from '../../lib/healthHub';
import { isKidMode } from '../../lib/healthSwitches';
import { Toggle } from '../ui/Toggle';
import type { HealthMetricSwitch, HealthProfile, HealthShare } from '../../types/api';

const ACTIVITY_OPTIONS: Array<[HealthProfile['activity_level'], string]> = [
  ['sedentary', 'Mostly sitting'],
  ['light', 'Light movement'],
  ['moderate', 'Moderate exercise'],
  ['active', 'Active most days'],
  ['very_active', 'Hard training'],
];

const GOAL_OPTIONS: Array<[HealthProfile['goal'], string]> = [
  ['maintain', 'Maintain'],
  ['lose', 'Lose gently (adults)'],
  ['gain', 'Gain'],
  ['balance', 'Balance (kids)'],
];

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-[11px] text-slate-500">
      {label}
      {children}
      {hint && <span className="mt-1 block text-[10px] text-slate-600">{hint}</span>}
    </label>
  );
}


interface ProfileFormState {
  display_name: string;
  birth_year: string;
  height_cm: string;
  sex: HealthProfile['sex'];
  activity_level: HealthProfile['activity_level'];
  goal: HealthProfile['goal'];
  units: HealthProfile['units'];
  kid_mode: boolean;
  sleep_hours: string;
  water_goal_ml: string;
  weekly_active_minutes: string;
}

/** Saved values, or honest empties when the profile has never been set up. */
function formFromProfile(profile: HealthProfile | undefined): ProfileFormState {
  return {
    display_name: profile?.display_name ?? '',
    birth_year: profile?.birth_year ? String(profile.birth_year) : '',
    height_cm: profile?.height_cm ? String(profile.height_cm) : '',
    sex: profile?.sex ?? 'unspecified',
    activity_level: profile?.activity_level ?? 'moderate',
    goal: profile?.goal ?? 'maintain',
    units: profile?.units ?? 'imperial',
    kid_mode: profile?.kid_mode ?? false,
    sleep_hours: profile?.sleep_need_minutes ? String(profile.sleep_need_minutes / 60) : '',
    water_goal_ml: profile?.water_goal_ml ? String(profile.water_goal_ml) : '',
    weekly_active_minutes: profile?.weekly_active_minutes
      ? String(profile.weekly_active_minutes)
      : '',
  };
}

/**
 * The Health tab: the profile that makes the maths honest, the measurements
 * that change over time, and the sharing switches.
 *
 * Weight is a metric, not a profile field: it belongs in history, carries its
 * source, and is never overwritten by a settings save.
 */
export default function HealthProfilePanel() {
  const queryClient = useQueryClient();
  const day = useMemo(() => localDay(), []);
  // Edits are a sparse overlay on the saved profile. Deriving the form this
  // way means a refetch can never clobber a half-finished edit, and there is no
  // effect copying server state into local state on every render.
  const [edits, setEdits] = useState<Partial<ProfileFormState>>({});
  const [weight, setWeight] = useState('');
  const [weightUnit, setWeightUnit] = useState<'kg' | 'lb'>('lb');
  const [restingHr, setRestingHr] = useState('');
  const [shareViewer, setShareViewer] = useState('');
  const [shareScope, setShareScope] = useState<HealthShare['scope']>('sleep');

  const profileQuery = useQuery({
    queryKey: ['health-profile'],
    queryFn: () => api.getHealthProfile(),
  });
  const summaryQuery = useQuery({
    queryKey: ['health-summary', day],
    queryFn: () => api.getHealthSummary(day),
    staleTime: 60_000,
  });
  const sharesQuery = useQuery({
    queryKey: ['health-shares'],
    queryFn: () => api.getHealthShares(),
  });
  const familyQuery = useQuery({
    queryKey: ['family-directory'],
    queryFn: () => api.getFamilyDirectory(),
    staleTime: 300_000,
  });

  const profile = profileQuery.data;
  const form: ProfileFormState = { ...formFromProfile(profile), ...edits };

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['health-profile'] });
    void queryClient.invalidateQueries({ queryKey: ['health-summary'] });
    void queryClient.invalidateQueries({ queryKey: ['health-nutrition'] });
    void queryClient.invalidateQueries({ queryKey: ['health-family'] });
    // Sex decides whether the cycle panel exists at all, so a profile save
    // must re-evaluate it in place.
    void queryClient.invalidateQueries({ queryKey: ['health-cycle'] });
  };

  const saveProfile = useMutation({
    mutationFn: () => {
      const sleepHours = Number(form.sleep_hours);
      return api.updateHealthProfile({
        display_name: form.display_name.trim(),
        birth_year: form.birth_year ? Number(form.birth_year) : null,
        height_cm: form.height_cm ? Number(form.height_cm) : null,
        sex: form.sex,
        activity_level: form.activity_level,
        goal: form.goal,
        units: form.units,
        kid_mode: form.kid_mode,
        sleep_need_minutes:
          Number.isFinite(sleepHours) && sleepHours > 0 ? Math.round(sleepHours * 60) : null,
        water_goal_ml: form.water_goal_ml ? Number(form.water_goal_ml) : null,
        weekly_active_minutes: form.weekly_active_minutes
          ? Number(form.weekly_active_minutes)
          : null,
      });
    },
    onSuccess: () => {
      toast.success('Health profile saved');
      setEdits({});
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not save the profile'),
  });

  const logMetric = useMutation({
    mutationFn: ({ metric, value, unit }: { metric: string; value: number; unit: string }) =>
      api.addHealthMetrics([
        {
          metric,
          value,
          unit,
          recorded_at: new Date().toISOString(),
          source: 'manual',
        },
      ]),
    onSuccess: () => {
      toast.success('Reading saved');
      setWeight('');
      setRestingHr('');
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not save that reading'),
  });

  const share = useMutation({
    mutationFn: (body: { viewer: string; scope: HealthShare['scope']; enabled: boolean }) =>
      api.updateHealthShare(body),
    onSuccess: () => {
      toast.success('Sharing updated');
      void queryClient.invalidateQueries({ queryKey: ['health-shares'] });
      void queryClient.invalidateQueries({ queryKey: ['health-family'] });
      setShareViewer('');
    },
    onError: (error: Error) => toast.error(error.message || 'Could not update sharing'),
  });

  const switchMetric = useMutation({
    mutationFn: ({ key, value }: { key: HealthMetricSwitch; value: boolean }) =>
      api.updateHealthProfile({ metrics: { [key]: value } as HealthProfile['metrics'] }),
    onSuccess: (saved) => {
      queryClient.setQueryData(['health-profile'], saved);
      void queryClient.invalidateQueries({ queryKey: ['health-summary'] });
    },
    onError: (error: Error) => toast.error(error.message || 'Could not change that card'),
  });

  // Kid mode as saved, not as half-edited: hiding follows the stored profile.
  const kid = isKidMode(profile);
  const latestWeight = summaryQuery.data?.metrics.weight;
  const latestRhr = summaryQuery.data?.metrics.resting_hr;
  const shares = sharesQuery.data?.shares ?? [];
  const me = profile?.user_id ?? '';
  const family = (familyQuery.data ?? []).filter((member) => member.username.toLowerCase() !== me);

  const exportData = async () => {
    try {
      const data = await api.exportHealth();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `jarvis-health-${day}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not export health data');
    }
  };

  return (
    <div className="space-y-3 sm:space-y-4" data-testid="health-profile-panel">
      <section className="glass-panel rounded-2xl border border-white/5 p-4 sm:p-5" aria-labelledby="profile-heading">
        <div className="flex items-center justify-between gap-3">
          <h2
            id="profile-heading"
            className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
          >
            <UserRound size={13} className="text-purple-300" />
            Health profile
          </h2>
          {!profile?.exists && <span className="text-[10px] text-amber-300">Not set up yet</span>}
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Used only for your own ranges and scores. Every field is optional; missing inputs are named
          instead of guessed.
        </p>

        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Field label="Display name">
            <input
              type="text"
              value={form.display_name}
              onChange={(event) => setEdits((prev) => ({ ...prev, display_name: event.target.value }))}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </Field>
          <Field label="Birth year">
            <input
              type="number"
              min={1900}
              max={new Date().getFullYear()}
              value={form.birth_year}
              onChange={(event) => setEdits((prev) => ({ ...prev, birth_year: event.target.value }))}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </Field>
          <Field label="Height (cm)">
            <input
              type="number"
              min={50}
              max={250}
              value={form.height_cm}
              onChange={(event) => setEdits((prev) => ({ ...prev, height_cm: event.target.value }))}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </Field>
          <Field label="Sex">
            <select
              value={form.sex ?? 'unspecified'}
              onChange={(event) =>
                setEdits((prev) => ({ ...prev, sex: event.target.value as HealthProfile['sex'] }))
              }
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            >
              <option value="unspecified">Not set</option>
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
          </Field>
          <Field label="Activity">
            <select
              value={form.activity_level}
              onChange={(event) =>
                setEdits((prev) => ({ ...prev, activity_level: event.target.value as HealthProfile['activity_level'] }))
              }
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            >
              {ACTIVITY_OPTIONS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Goal">
            <select
              value={form.goal}
              onChange={(event) => setEdits((prev) => ({ ...prev, goal: event.target.value as HealthProfile['goal'] }))}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            >
              {GOAL_OPTIONS.filter(([value]) => !(form.kid_mode && value === 'lose')).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Sleep need (hours)">
            <input
              type="number"
              min={4}
              max={14}
              step={0.25}
              value={form.sleep_hours}
              onChange={(event) => setEdits((prev) => ({ ...prev, sleep_hours: event.target.value }))}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </Field>
          <Field label="Water goal (ml)">
            <input
              type="number"
              min={250}
              max={10000}
              step={250}
              value={form.water_goal_ml}
              onChange={(event) => setEdits((prev) => ({ ...prev, water_goal_ml: event.target.value }))}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </Field>
          <Field label="Weekly active minutes">
            <input
              type="number"
              min={0}
              max={2000}
              step={10}
              value={form.weekly_active_minutes}
              onChange={(event) => setEdits((prev) => ({ ...prev, weekly_active_minutes: event.target.value }))}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            />
          </Field>
          <Field label="Units">
            <select
              value={form.units}
              onChange={(event) => setEdits((prev) => ({ ...prev, units: event.target.value as HealthProfile['units'] }))}
              className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
            >
              <option value="imperial">Imperial</option>
              <option value="metric">Metric</option>
            </select>
          </Field>
          <label className="col-span-2 flex items-center gap-2 self-end rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5 text-xs text-slate-300 sm:col-span-2">
            <input
              type="checkbox"
              checked={form.kid_mode || profile?.kid_mode_locked === true}
              disabled={profile?.kid_mode_locked === true}
              onChange={(event) => setEdits((prev) => ({ ...prev, kid_mode: event.target.checked }))}
              className="h-4 w-4 accent-purple-500 disabled:opacity-60"
            />
            <span>
              Kid mode - guidance ranges only, never deficits or weight-loss targets
              {profile?.kid_mode_locked && <span className="block text-[11px] text-slate-500">Always on for anyone under 18.</span>}
            </span>
          </label>
        </div>

        <button
          type="button"
          onClick={() => saveProfile.mutate()}
          disabled={saveProfile.isPending}
          data-testid="save-health-profile"
          className="glass-button mt-3 flex min-h-11 w-full items-center justify-center gap-2 text-xs font-semibold disabled:opacity-50"
        >
          {saveProfile.isPending ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
          Save profile
        </button>
      </section>

      <section
        className="glass-panel rounded-2xl border border-white/5 p-4"
        aria-labelledby="cards-heading"
        data-testid="health-metric-switches"
      >
        <h3 id="cards-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          <LayoutGrid size={13} className="text-purple-300" />
          Cards on your Health page
        </h3>
        <p className="mt-1 text-[11px] text-slate-500">
          Turn off anything you do not want to see. Your data is kept, family sharing is unchanged, and
          the coach stops bringing it up.
        </p>
        {profileQuery.isLoading ? (
          <div className="mt-2 h-20 skeleton rounded-xl" aria-hidden />
        ) : profileQuery.isError || !profile ? (
          <p className="mt-2 text-xs text-amber-300">Your card settings could not be loaded.</p>
        ) : (
          <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {profile.metric_catalog.map((entry) => (
              <div
                key={entry.key}
                className="flex min-h-11 items-center justify-between gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 text-xs text-slate-300"
              >
                {entry.label}
                <Toggle
                  checked={profile.metrics[entry.key] === true}
                  disabled={switchMetric.isPending}
                  onChange={(value) => switchMetric.mutate({ key: entry.key, value })}
                  ariaLabel={entry.label}
                  testId={`metric-switch-${entry.key}`}
                />
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="measures-heading">
        <h3 id="measures-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          <Scale size={13} className="text-emerald-300" />
          Measurements
        </h3>
        <div className="mt-2 grid grid-cols-2 gap-3">
          {!kid && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-slate-500">Weight</p>
              <p className="mt-0.5 text-sm font-semibold text-white">
                {latestWeight ? `${latestWeight.value.toFixed(1)} ${latestWeight.unit}` : 'No reading'}
              </p>
              {latestWeight && (
                <p className="text-[10px] text-slate-500">
                  {formatDayLabel(latestWeight.recorded_at.slice(0, 10))} · {latestWeight.source}
                </p>
              )}
            </div>
          )}
          <div>
            <p className="text-[10px] uppercase tracking-wider text-slate-500">Resting heart rate</p>
            <p className="mt-0.5 text-sm font-semibold text-white">
              {latestRhr ? `${Math.round(latestRhr.value)} bpm` : 'No reading'}
            </p>
            {latestRhr && (
              <p className="text-[10px] text-slate-500">
                {formatDayLabel(latestRhr.recorded_at.slice(0, 10))} · {latestRhr.source}
                {summaryQuery.data?.resting_hr_baseline
                  ? ` · baseline ${Math.round(summaryQuery.data.resting_hr_baseline)} bpm`
                  : ''}
              </p>
            )}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-end gap-2">
          {!kid && (
            <>
              <label className="text-[11px] text-slate-500">
                Log weight
                <div className="mt-1 flex gap-1">
                  <input
                    type="number"
                    min={1}
                    step={0.1}
                    value={weight}
                    onChange={(event) => setWeight(event.target.value)}
                    className="glass-input h-10 w-24 px-2 text-xs pointer-coarse:h-11"
                  />
                  <select
                    value={weightUnit}
                    onChange={(event) => setWeightUnit(event.target.value as 'kg' | 'lb')}
                    aria-label="Weight unit"
                    className="glass-input h-10 px-1 text-xs pointer-coarse:h-11"
                  >
                    <option value="lb">lb</option>
                    <option value="kg">kg</option>
                  </select>
                </div>
              </label>
              <button
                type="button"
                onClick={() => {
                  const value = Number(weight);
                  if (!Number.isFinite(value) || value <= 0) {
                    toast.error('Enter a weight');
                    return;
                  }
                  logMetric.mutate({ metric: 'weight', value, unit: weightUnit });
                }}
                disabled={logMetric.isPending}
                className="glass-button min-h-10 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-11"
              >
                Save
              </button>
            </>
          )}
          <label className="text-[11px] text-slate-500">
            Log resting HR
            <div className="mt-1 flex gap-1">
              <input
                type="number"
                min={20}
                max={250}
                value={restingHr}
                onChange={(event) => setRestingHr(event.target.value)}
                className="glass-input h-10 w-20 px-2 text-xs pointer-coarse:h-11"
              />
              <span className="flex h-10 items-center text-[10px] text-slate-500">bpm</span>
            </div>
          </label>
          <button
            type="button"
            onClick={() => {
              const value = Number(restingHr);
              if (!Number.isFinite(value) || value <= 0) {
                toast.error('Enter a heart rate');
                return;
              }
              logMetric.mutate({ metric: 'resting_hr', value, unit: 'bpm' });
            }}
            disabled={logMetric.isPending}
            className="glass-button min-h-10 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-11"
          >
            Save
          </button>
        </div>
        <p className="mt-2 text-[10px] text-slate-600">
          Readings are stored with their source and never overwritten. A future watch or Health
          Connect import writes to the same history.
        </p>
      </section>

      <section className="glass-panel rounded-2xl border border-white/5 p-4" aria-labelledby="sharing-heading">
        <h3 id="sharing-heading" className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          <Share2 size={13} className="text-sky-300" />
          Family sharing
        </h3>
        <p className="mt-1 text-[11px] text-slate-500">
          Off by default. Share only the scopes you choose; nobody can browse your profile or other
          metrics from this.
        </p>
        {shares.length === 0 ? (
          <p className="mt-2 text-xs text-slate-500">Nothing shared.</p>
        ) : (
          <div className="mt-2 flex flex-wrap gap-2">
            {shares.map((entry) => (
              <span
                key={`${entry.viewer}-${entry.scope}`}
                className="flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] capitalize text-slate-300"
              >
                {entry.viewer} · {entry.scope}
                <button
                  type="button"
                  aria-label={`Stop sharing ${entry.scope} with ${entry.viewer}`}
                  onClick={() =>
                    share.mutate({ viewer: entry.viewer, scope: entry.scope, enabled: false })
                  }
                  className="text-slate-500 hover:text-red-300"
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-[11px] text-slate-500">
            Person
            <select
              value={shareViewer}
              onChange={(event) => setShareViewer(event.target.value)}
              className="glass-input mt-1 h-10 min-w-32 px-2 text-xs capitalize pointer-coarse:h-11"
            >
              <option value="">Choose…</option>
              {family.map((member) => (
                <option key={member.username} value={member.username}>
                  {member.display_name || member.username}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[11px] text-slate-500">
            Scope
            <select
              value={shareScope}
              onChange={(event) => setShareScope(event.target.value as HealthShare['scope'])}
              className="glass-input mt-1 h-10 px-2 text-xs pointer-coarse:h-11"
            >
              <option value="sleep">Sleep</option>
              <option value="nutrition">Nutrition</option>
              {/* An adult may open their own phone use; a minor's is the parents'. */}
              {!profile?.kid_mode && !profile?.kid_mode_locked && <option value="digital">Phone use</option>}
            </select>
          </label>
          <button
            type="button"
            onClick={() => {
              if (!shareViewer) {
                toast.error('Choose a person');
                return;
              }
              share.mutate({ viewer: shareViewer, scope: shareScope, enabled: true });
            }}
            disabled={share.isPending || !shareViewer}
            className="glass-button min-h-10 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-11"
          >
            Share
          </button>
        </div>
        <p className="mt-2 text-[10px] text-slate-600">
          The existing Family Activity feed (Fitness tab) still covers steps, workouts and badges.
        </p>
      </section>

      <section className="glass-panel rounded-2xl border border-white/5 p-4">
        <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
          <HeartPulse size={13} className="text-purple-300" />
          Your data
        </h3>
        <p className="mt-1 text-[11px] text-slate-500">
          Everything this service stores for you - profile, readings, sleep, check-ins and food - in
          one JSON file.
        </p>
        <button
          type="button"
          onClick={() => void exportData()}
          className="glass-button mt-2 flex min-h-10 items-center gap-2 px-3 text-xs font-semibold pointer-coarse:min-h-11"
        >
          <Download size={13} />
          Download my health data
        </button>
      </section>
    </div>
  );
}
