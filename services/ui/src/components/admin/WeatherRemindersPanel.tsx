import { useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CloudRain, Send, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import type { SavedPlace, UserProfile } from '../../types/api';
import { api } from '../../services/api';
import EntitySearchDropdown from '../ui/EntitySearchDropdown';
import HelpTooltip from '../ui/HelpTooltip';
import { confirmDialog } from '../ui/confirmDialog';
import {
  WEATHER_KINDS,
  loadWeatherSettings,
  newReminderId,
  parseLocation,
  removeReminder,
  saveWeatherPlace,
  saveWeatherReminders,
  unitSuffix,
  upsertReminder,
  type WeatherLocation,
  type WeatherAction,
  type WeatherReminder,
  type WeatherReminderKind,
  type WeatherSettings,
  type WeatherUnits,
} from '../../lib/weather';

/**
 * Admin: where the household forecast is for, and the weather reminders that
 * go to phones ("Cover the hay" when rain is likely, "Drip the taps" before a
 * freeze). Each reminder goes to the people picked, or everyone, and may watch
 * a saved place instead of home. The forecast is Open-Meteo's (docs/WEATHER.md).
 */

const KINDS = Object.keys(WEATHER_KINDS) as WeatherReminderKind[];
const QUERY_KEY = ['weather-settings'];

type Draft = {
  id: string;
  kind: WeatherReminderKind;
  title: string;
  note: string;
  hours: string;
  threshold: string;
  place: string;
  people: string[];
  enabled: boolean;
  notify: boolean;
  /** One Home Assistant action, e.g. cover.close_cover on cover.barn_door. */
  haService: string;
  haEntity: string;
  /** Other actions on the rule (calls to services), kept as they are. */
  otherActions: WeatherAction[];
};

const emptyDraft = (): Draft => ({
  id: '', kind: 'rain', title: '', note: '', hours: '', threshold: '', place: 'home', people: [], enabled: true,
  notify: true, haService: '', haEntity: '', otherActions: [],
});

const actionLabel = (a: WeatherAction) =>
  a.type === 'ha_service' ? `runs ${a.service}${a.entity_id ? ` on ${a.entity_id}` : ''}` : `calls ${a.service}${a.path}`;

const placeKey = (loc: WeatherLocation | null | undefined) =>
  loc ? `${loc.latitude.toFixed(4)},${loc.longitude.toFixed(4)}` : 'home';

/** Where the forecast is for, and its units; starts from the saved values. */
const ForecastPlaceForm: FC<{
  initial: WeatherSettings;
  saving: boolean;
  onSave: (location: WeatherLocation | null, units: WeatherUnits) => void;
}> = ({ initial, saving, onSave }) => {
  const [locName, setLocName] = useState(initial.location?.name ?? '');
  const [lat, setLat] = useState(initial.location ? String(initial.location.latitude) : '');
  const [lon, setLon] = useState(initial.location ? String(initial.location.longitude) : '');
  const [units, setUnits] = useState<WeatherUnits>(initial.units);

  const fillFromDevice = () => {
    if (!navigator.geolocation) {
      toast.error('This device cannot share its location');
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLat(pos.coords.latitude.toFixed(4));
        setLon(pos.coords.longitude.toFixed(4));
      },
      () => toast.error('Could not read this device\'s location'),
      { timeout: 10000 },
    );
  };

  const saveLocation = () => {
    if (!lat.trim() && !lon.trim()) {
      onSave(null, units);
      return;
    }
    const location = parseLocation({ name: locName, latitude: lat, longitude: lon });
    if (!location) {
      toast.error('Latitude must be -90 to 90 and longitude -180 to 180');
      return;
    }
    onSave(location, units);
  };

  return (
    <fieldset className="mb-6 rounded-xl border border-white/5 bg-black/20 p-3">
      <legend className="px-1 text-[11px] font-semibold text-slate-300">Forecast location and units</legend>
      <p className="mb-3 text-[11px] text-slate-500">
        Leave latitude and longitude empty to use Home Assistant&apos;s home zone.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_120px_120px_auto]">
        <input
          value={locName}
          onChange={(e) => setLocName(e.target.value)}
          placeholder="Name (Home)"
          aria-label="Forecast location name"
          className="glass-input bg-black/30"
        />
        <input
          value={lat}
          onChange={(e) => setLat(e.target.value)}
          inputMode="decimal"
          placeholder="Latitude"
          aria-label="Forecast latitude"
          className="glass-input bg-black/30"
        />
        <input
          value={lon}
          onChange={(e) => setLon(e.target.value)}
          inputMode="decimal"
          placeholder="Longitude"
          aria-label="Forecast longitude"
          className="glass-input bg-black/30"
        />
        <button
          type="button"
          onClick={fillFromDevice}
          className="glass-button px-3 py-2 text-[10px] font-black uppercase tracking-widest pointer-coarse:min-h-11"
        >
          Use mine
        </button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs text-slate-400">
          Units
          <select
            value={units}
            onChange={(e) => setUnits(e.target.value as WeatherUnits)}
            aria-label="Weather units"
            className="glass-input bg-black/30"
          >
            <option value="imperial">°F, mph, inches</option>
            <option value="metric">°C, km/h, mm</option>
          </select>
        </label>
        <button
          type="button"
          disabled={saving}
          onClick={saveLocation}
          className="glass-button px-4 py-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-40 pointer-coarse:min-h-11"
        >
          Save location
        </button>
      </div>
    </fieldset>
  );
};

export const WeatherRemindersPanel: FC<{ users: UserProfile[] }> = ({ users }) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(emptyDraft);

  const { data, isPending, error } = useQuery<WeatherSettings>({ queryKey: QUERY_KEY, queryFn: loadWeatherSettings });
  const { data: places = [] } = useQuery<SavedPlace[]>({ queryKey: ['saved-places'], queryFn: api.getSavedPlaces });
  const reminders = data?.reminders ?? [];
  // Thresholds are in the saved units (what the server compares against).
  const units: WeatherUnits = data?.units ?? 'imperial';
  const family = users.filter((u) => !u.is_system_default);
  const info = WEATHER_KINDS[draft.kind];

  const placeOptions: { key: string; label: string; location: WeatherLocation | null }[] = [
    { key: 'home', label: 'Home (the forecast location)', location: null },
    ...places.map((p) => {
      const location = { name: p.name, latitude: p.lat, longitude: p.lon };
      return { key: placeKey(location), label: p.name, location };
    }),
  ];
  // A rule's place that is no longer (or never was) a saved place stays
  // choosable, so editing the rule does not quietly move it to Home.
  for (const r of reminders) {
    if (r.location && !placeOptions.some((o) => o.key === placeKey(r.location))) {
      placeOptions.push({ key: placeKey(r.location), label: r.location.name ?? 'Custom place', location: r.location });
    }
  }

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    queryClient.invalidateQueries({ queryKey: ['weather'] });
  };

  const saveRules = useMutation({
    mutationFn: (next: WeatherReminder[]) => saveWeatherReminders(next, units),
    onSuccess: invalidate,
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to save weather reminders'),
  });

  const savePlace = useMutation({
    mutationFn: ({ location, unitSystem }: { location: WeatherLocation | null; unitSystem: WeatherUnits }) =>
      saveWeatherPlace(location, unitSystem, reminders, units),
    onSuccess: () => {
      invalidate();
      toast.success('Forecast location saved');
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to save the forecast location'),
  });

  const test = useMutation({
    mutationFn: (rule: WeatherReminder) => api.testWeatherReminder(rule),
    onSuccess: (res) =>
      res.sent ? toast.success(`Sent to your phone: ${res.body}`) : toast.error('The notice was not delivered'),
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Test failed'),
  });

  const toRule = (d: Draft): WeatherReminder => ({
    id: d.id || newReminderId(),
    kind: d.kind,
    title: d.title,
    note: d.note,
    people: d.people,
    hours: d.hours ? Number(d.hours) : undefined,
    threshold: d.threshold ? Number(d.threshold) : undefined,
    location: placeOptions.find((o) => o.key === d.place)?.location ?? null,
    enabled: d.enabled,
    notify: d.notify,
    actions: [
      ...(d.haService.trim()
        ? [{ type: 'ha_service' as const, service: d.haService.trim().toLowerCase(), ...(d.haEntity.trim() ? { entity_id: d.haEntity.trim() } : {}) }]
        : []),
      ...d.otherActions,
    ],
  });

  const submit = () => {
    if (draft.haService.trim() && !/^[a-z_]+\.[a-z0-9_]+$/.test(draft.haService.trim().toLowerCase())) {
      toast.error('A Home Assistant action looks like cover.close_cover');
      return;
    }
    if (!draft.notify && !draft.haService.trim() && !draft.otherActions.length) {
      toast.error('Turn on the notice or add an action, or the reminder does nothing');
      return;
    }
    const rule = toRule(draft);
    saveRules.mutate(upsertReminder(reminders, rule), {
      onSuccess: () => {
        toast.success(`"${rule.title || info.title}" saved`);
        setDraft(emptyDraft());
      },
    });
  };

  const edit = (rule: WeatherReminder) => {
    const ha = (rule.actions ?? []).find((a) => a.type === 'ha_service');
    setDraft({
      id: rule.id,
      kind: rule.kind,
      title: rule.title,
      note: rule.note ?? '',
      hours: rule.hours ? String(rule.hours) : '',
      threshold: rule.threshold !== undefined ? String(rule.threshold) : '',
      place: placeKey(rule.location),
      people: rule.people,
      enabled: rule.enabled !== false,
      notify: rule.notify !== false,
      haService: ha?.type === 'ha_service' ? ha.service : '',
      haEntity: ha?.type === 'ha_service' ? ha.entity_id ?? '' : '',
      otherActions: (rule.actions ?? []).filter((a) => a !== ha),
    });
  };

  const togglePerson = (username: string) =>
    setDraft((d) => ({
      ...d,
      people: d.people.includes(username) ? d.people.filter((p) => p !== username) : [...d.people, username],
    }));

  const placeName = (rule: WeatherReminder) => rule.location?.name ?? (rule.location ? 'Custom place' : 'Home');
  const suffix = unitSuffix(info.measure, units);

  return (
    <section className="glass-panel min-w-0 overflow-hidden p-4 sm:p-6" data-testid="weather-reminders">
      <div className="mb-6 flex items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <CloudRain size={20} className="text-sky-300" />
            Weather reminders
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            Forecasts from Open-Meteo. Pick the weather that should reach someone&apos;s phone, and who hears it.
          </p>
        </div>
        <HelpTooltip docName="WEATHER.md" sectionTitle="## Weather reminders" label="Weather reminders" />
      </div>

      {data && (
        <ForecastPlaceForm
          key={`${placeKey(data.location)}:${data.location?.name ?? ''}:${data.units}`}
          initial={data}
          saving={savePlace.isPending}
          onSave={(location, unitSystem) => savePlace.mutate({ location, unitSystem })}
        />
      )}

      <div className="mb-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[200px_minmax(0,1fr)_minmax(0,1fr)]">
        <select
          value={draft.kind}
          aria-label="Weather reminder kind"
          onChange={(e) => setDraft((d) => ({ ...d, kind: e.target.value as WeatherReminderKind, threshold: '', hours: '' }))}
          className="glass-input bg-black/30"
        >
          {KINDS.map((key) => (
            <option key={key} value={key}>{WEATHER_KINDS[key].label}</option>
          ))}
        </select>
        <input
          value={draft.title}
          onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
          placeholder={`Title (${info.title}), e.g. Cover the hay`}
          aria-label="Weather reminder title"
          maxLength={80}
          className="glass-input bg-black/30"
        />
        <input
          value={draft.note}
          onChange={(e) => setDraft((d) => ({ ...d, note: e.target.value }))}
          placeholder="Note (optional), e.g. Tarps are in the barn"
          aria-label="Weather reminder note"
          maxLength={200}
          className="glass-input bg-black/30"
        />
      </div>

      <p className="mb-3 text-[11px] text-slate-500">{info.hint}</p>

      <div className="mb-4 flex flex-wrap gap-3">
        <label className="flex items-center gap-2 text-xs text-slate-400">
          {info.thresholdLabel}
          <input
            type="number"
            step="any"
            value={draft.threshold}
            placeholder={String(info.threshold[units])}
            onChange={(e) => setDraft((d) => ({ ...d, threshold: e.target.value }))}
            aria-label="Weather reminder threshold"
            className="glass-input w-24 bg-black/30"
          />
          {suffix}
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          Look ahead
          <input
            type="number"
            min={1}
            max={48}
            value={draft.hours}
            placeholder={String(info.hours)}
            onChange={(e) => setDraft((d) => ({ ...d, hours: e.target.value }))}
            aria-label="Weather reminder hours ahead"
            className="glass-input w-20 bg-black/30"
          />
          hours
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          Where
          <select
            value={draft.place}
            onChange={(e) => setDraft((d) => ({ ...d, place: e.target.value }))}
            aria-label="Weather reminder place"
            className="glass-input bg-black/30"
          >
            {placeOptions.map((o) => (
              <option key={o.key} value={o.key}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="flex min-h-11 items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft((d) => ({ ...d, enabled: e.target.checked }))}
            aria-label="Weather reminder on"
            className="h-4 w-4 accent-sky-500"
          />
          On
        </label>
        <label className="flex min-h-11 items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={draft.notify}
            onChange={(e) => setDraft((d) => ({ ...d, notify: e.target.checked }))}
            aria-label="Weather reminder sends a notice"
            className="h-4 w-4 accent-sky-500"
          />
          Send a notice
        </label>
      </div>

      <fieldset className="mb-4 rounded-xl border border-white/5 bg-black/20 p-3">
        <legend className="px-1 text-[11px] font-semibold text-slate-300">
          Also run a Home Assistant action <span className="text-slate-500">(optional)</span>
        </legend>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,200px)_minmax(0,1fr)]">
          <input
            value={draft.haService}
            onChange={(e) => setDraft((d) => ({ ...d, haService: e.target.value }))}
            placeholder="cover.close_cover"
            aria-label="Home Assistant action"
            autoCapitalize="none"
            className="glass-input bg-black/30"
          />
          <EntitySearchDropdown
            value={draft.haEntity}
            onChange={(v) => setDraft((d) => ({ ...d, haEntity: v }))}
            placeholder="Entity it acts on (optional)"
            testId="weather-action-entity"
          />
        </div>
        <p className="mt-2 text-[11px] text-slate-500">
          Runs once when the weather starts, e.g. close the barn door or turn off the sprinklers.
        </p>
      </fieldset>

      <fieldset className="mb-4 rounded-xl border border-white/5 bg-black/20 p-3">
        <legend className="px-1 text-[11px] font-semibold text-slate-300">
          Who is reminded {draft.people.length === 0 && <span className="text-slate-500">(nobody picked: everyone)</span>}
        </legend>
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {family.map((user) => {
            const name = user.username.toLowerCase();
            return (
              <li key={user.username}>
                <label className="flex min-h-11 items-center justify-between gap-3 rounded-lg border border-white/5 bg-black/20 p-2">
                  <span className="text-xs text-slate-300">@{user.username}</span>
                  <input
                    type="checkbox"
                    checked={draft.people.includes(name)}
                    onChange={() => togglePerson(name)}
                    aria-label={`Remind @${user.username}`}
                    className="h-4 w-4 accent-sky-500"
                  />
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>

      <div className="mb-6 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={saveRules.isPending}
          onClick={submit}
          className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest disabled:opacity-40 pointer-coarse:min-h-11"
        >
          {draft.id ? 'Save changes' : 'Add reminder'}
        </button>
        {draft.id && (
          <button
            type="button"
            onClick={() => setDraft(emptyDraft())}
            className="rounded-xl px-4 py-3 text-[10px] font-black uppercase tracking-widest text-slate-400 hover:text-white pointer-coarse:min-h-11"
          >
            Cancel
          </button>
        )}
      </div>

      <div className="max-h-[28rem] space-y-3 overflow-y-auto pr-2">
        {isPending && <p className="text-xs text-slate-400">Loading weather reminders…</p>}
        {error && (
          <p className="text-xs text-red-300">
            Could not load weather reminders: {error instanceof Error ? error.message : 'unknown error'}
          </p>
        )}
        {!isPending && !error && reminders.length === 0 && (
          <p className="text-xs text-slate-500">No weather reminders yet.</p>
        )}
        {reminders.map((rule) => {
          const kind = WEATHER_KINDS[rule.kind];
          const threshold = rule.threshold ?? kind.threshold[units];
          return (
            <div key={rule.id} className="glass-card flex flex-wrap items-center justify-between gap-3 p-4">
              <div className="min-w-0 flex-1">
                <p className={`truncate text-sm font-semibold ${rule.enabled === false ? 'text-slate-500 line-through' : 'text-white'}`}>
                  {rule.title}
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  {kind.label} · {kind.thresholdLabel.toLowerCase()} {threshold}{unitSuffix(kind.measure, units)}
                  {' · '}next {rule.hours ?? kind.hours} h · {placeName(rule)} ·{' '}
                  {rule.notify === false ? 'no notice' : rule.people.length ? rule.people.map((p) => `@${p}`).join(', ') : 'everyone'}
                  {(rule.actions ?? []).map((a) => ` · ${actionLabel(a)}`).join('')}
                </p>
                {rule.note && <p className="mt-1 truncate text-[11px] text-slate-500">{rule.note}</p>}
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => edit(rule)}
                  className="glass-button px-3 py-2 text-[10px] font-black uppercase tracking-widest pointer-coarse:min-h-11"
                >
                  Edit
                </button>
                <button
                  type="button"
                  disabled={test.isPending}
                  onClick={() => test.mutate(rule)}
                  className="rounded-xl p-2 text-slate-400 transition hover:bg-sky-500/10 hover:text-sky-300 disabled:opacity-40 pointer-coarse:min-h-11"
                  aria-label={`Send ${rule.title} to my phone now`}
                  title="Send to my phone now"
                >
                  <Send size={16} />
                </button>
                <button
                  type="button"
                  disabled={saveRules.isPending}
                  onClick={async () => {
                    const ok = await confirmDialog({
                      title: 'Remove this reminder?',
                      body: `Stop sending "${rule.title}"?`,
                      confirmLabel: 'Remove',
                      danger: true,
                    });
                    if (ok) saveRules.mutate(removeReminder(reminders, rule.id));
                  }}
                  className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300 disabled:opacity-40 pointer-coarse:min-h-11"
                  aria-label={`Remove ${rule.title}`}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
};

export default WeatherRemindersPanel;
