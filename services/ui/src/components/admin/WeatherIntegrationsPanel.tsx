import { useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link2, Trash2, TrendingUp } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import EntitySearchDropdown from '../ui/EntitySearchDropdown';
import HelpTooltip from '../ui/HelpTooltip';
import { confirmDialog } from '../ui/confirmDialog';
import {
  METRIC_MODES, WEATHER_EVENTS, WEATHER_KINDS,
  loadIntegrationSettings, metricName, newReminderId, saveHooks, saveSensorMetrics,
  type WeatherEvent, type WeatherHook, type WeatherIntegrationSettings, type WeatherMetricMode,
  type WeatherReminderKind, type WeatherSensorMetric,
} from '../../lib/weather';

/**
 * Admin: tie the weather to everything else (docs/WEATHER.md).
 *
 * - **Insights**: every tracked metric against the weather, in plain sentences.
 * - **Track with weather**: any Home Assistant sensor (solar, a water meter, a
 *   pump, barn humidity) becomes a metric, analysed like energy is.
 * - **Send weather to services**: weather events delivered to any service this
 *   deployment knows, picked from the server's own list, so a new service
 *   shows up here with no change to this screen.
 *
 * Everything here is optional: with the weather service away the panel says
 * so in one line and the rest of Admin is untouched.
 */

const SETTINGS_KEY = ['weather-integrations'];
const MODES = Object.keys(METRIC_MODES) as WeatherMetricMode[];
const EVENTS = Object.keys(WEATHER_EVENTS) as WeatherEvent[];
const KINDS = Object.keys(WEATHER_KINDS) as WeatherReminderKind[];

const unitOf = (unit: string) => (unit ? ` ${unit}` : '');

export const WeatherIntegrationsPanel: FC = () => {
  const queryClient = useQueryClient();
  const settings = useQuery<WeatherIntegrationSettings>({ queryKey: SETTINGS_KEY, queryFn: loadIntegrationSettings });
  const info = useQuery({ queryKey: ['weather', 'integrations'], queryFn: api.getWeatherIntegrations, retry: false });
  const insights = useQuery({
    queryKey: ['weather', 'correlations'],
    queryFn: () => api.getWeatherCorrelations(60),
    retry: false,
    staleTime: 10 * 60 * 1000,
  });

  const [label, setLabel] = useState('');
  const [entity, setEntity] = useState('');
  const [mode, setMode] = useState<WeatherMetricMode>('power');
  const [unit, setUnit] = useState('');

  const [events, setEvents] = useState<WeatherEvent[]>(['alert.started']);
  const [service, setService] = useState('');
  const [path, setPath] = useState('');
  const [kinds, setKinds] = useState<WeatherReminderKind[]>([]);

  const metrics = settings.data?.metrics ?? [];
  const hooks = settings.data?.hooks ?? [];
  const services = info.data?.services ?? [];

  const done = (msg: string) => {
    queryClient.invalidateQueries({ queryKey: SETTINGS_KEY });
    queryClient.invalidateQueries({ queryKey: ['weather'] });
    toast.success(msg);
  };
  const fail = (err: unknown) => toast.error(err instanceof Error ? err.message : 'Could not save');

  const saveMetrics = useMutation({ mutationFn: saveSensorMetrics, onError: fail });
  const saveHookList = useMutation({ mutationFn: saveHooks, onError: fail });

  const addMetric = () => {
    if (!label.trim() || !entity.includes('.')) {
      toast.error('Give it a name and pick a Home Assistant entity');
      return;
    }
    const name = metricName(label);
    if (metrics.some((m) => m.name === name) || ['energy_kwh', 'heat_hours', 'cool_hours', 'indoor_temp'].includes(name)) {
      toast.error(`"${label}" is already tracked`);
      return;
    }
    const metric: WeatherSensorMetric = { name, label: label.trim(), entity_id: entity, mode, ...(unit.trim() ? { unit: unit.trim() } : {}) };
    saveMetrics.mutate([...metrics, metric], {
      onSuccess: () => {
        done(`${metric.label} is now tracked against the weather`);
        setLabel('');
        setEntity('');
        setUnit('');
      },
    });
  };

  const addHook = () => {
    const target = service || services[0];
    if (!target || !/^\/[A-Za-z0-9._~/%-]{0,200}$/.test(path.trim()) || path.includes('..') || !events.length) {
      toast.error('Pick at least one event, a service and a path such as /api/hooks/weather');
      return;
    }
    const hook: WeatherHook = {
      id: newReminderId(), events, service: target, path: path.trim(), method: 'POST',
      filter: { kinds, rules: [] },
    };
    saveHookList.mutate([...hooks, hook], {
      onSuccess: () => {
        done(`${target} now receives weather events`);
        setPath('');
        setKinds([]);
      },
    });
  };

  const remove = async (what: string, run: () => void) => {
    if (await confirmDialog({ title: `Remove ${what}?`, body: 'This stops it straight away.', confirmLabel: 'Remove', danger: true })) run();
  };

  const toggle = <T,>(list: T[], item: T) => (list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);

  const unavailable = info.isError;

  return (
    <section className="glass-panel min-w-0 overflow-hidden p-4 sm:p-6" data-testid="weather-integrations">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <Link2 size={20} className="shrink-0 text-sky-300" />
            Weather integrations
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            Track anything against the weather, and send weather events to other services.
          </p>
        </div>
        <HelpTooltip docName="WEATHER.md" sectionTitle="## Using weather from a service" label="Weather integrations" />
      </div>

      {unavailable && (
        <p className="mb-4 rounded-xl border border-white/5 bg-black/20 p-3 text-xs text-slate-400" role="status">
          The weather service is not answering right now. Saved settings are kept and apply when it is back.
        </p>
      )}

      {/* Insights */}
      <div className="mb-6">
        <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold text-white">
          <TrendingUp size={14} className="text-emerald-300" /> What the weather is doing to things
        </h4>
        {insights.isPending && !unavailable && <p className="text-xs text-slate-500">Reading the last 60 days…</p>}
        {insights.data && insights.data.insights.length === 0 && (
          <p className="text-xs text-slate-500">
            Nothing clear yet. Insights appear once a few days are recorded with different weather.
          </p>
        )}
        {insights.data && insights.data.insights.length > 0 && (
          <ul className="space-y-1" data-testid="weather-insights">
            {insights.data.insights.map((line) => (
              <li key={line} className="text-xs text-slate-300">{line}</li>
            ))}
          </ul>
        )}
        {insights.data && insights.data.metrics.length > 0 && (
          <p className="mt-2 text-[11px] text-slate-500">
            Tracked: {insights.data.metrics.map((m) => `${m.label} (${m.days} d)`).join(', ')}
          </p>
        )}
      </div>

      {/* Sensors */}
      <fieldset className="mb-6 rounded-xl border border-white/5 bg-black/20 p-3">
        <legend className="px-1 text-[11px] font-semibold text-slate-300">Track a Home Assistant sensor with the weather</legend>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_200px_90px_auto]">
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Name, e.g. Solar"
            aria-label="Tracked metric name"
            maxLength={60}
            className="glass-input bg-black/30"
          />
          <EntitySearchDropdown value={entity} onChange={setEntity} placeholder="Sensor or switch…" testId="weather-metric-entity" />
          <select value={mode} onChange={(e) => setMode(e.target.value as WeatherMetricMode)} aria-label="How to read it" className="glass-input bg-black/30">
            {MODES.map((m) => <option key={m} value={m}>{METRIC_MODES[m].label}</option>)}
          </select>
          <input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="Unit" aria-label="Tracked metric unit" maxLength={12} className="glass-input bg-black/30" />
          <button
            type="button"
            onClick={addMetric}
            disabled={saveMetrics.isPending}
            className="glass-button px-4 py-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-40 pointer-coarse:min-h-11"
          >
            Track
          </button>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">{METRIC_MODES[mode].hint}</p>
        <ul className="mt-3 space-y-2">
          {metrics.map((m) => (
            <li key={m.name} className="flex items-center justify-between gap-3 rounded-lg border border-white/5 bg-black/20 p-2">
              <span className="min-w-0 text-xs text-slate-300">
                <span className="font-semibold text-white">{m.label}</span>
                <span className="block truncate text-slate-500 sm:inline">
                  {' '}{m.entity_id} · {METRIC_MODES[m.mode].label}{unitOf(m.unit ?? '')}
                </span>
              </span>
              <button
                type="button"
                onClick={() => remove(m.label, () => saveMetrics.mutate(metrics.filter((x) => x.name !== m.name), { onSuccess: () => done(`${m.label} removed`) }))}
                className="shrink-0 rounded-xl p-2 text-slate-400 hover:bg-red-500/10 hover:text-red-300 pointer-coarse:min-h-11"
                aria-label={`Stop tracking ${m.label}`}
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
        {(info.data?.metrics ?? []).some((m) => m.source === 'pushed') && (
          <p className="mt-2 text-[11px] text-slate-500">
            Sent by other services: {(info.data?.metrics ?? []).filter((m) => m.source === 'pushed').map((m) => m.label).join(', ')}
          </p>
        )}
      </fieldset>

      {/* Hooks */}
      <fieldset className="rounded-xl border border-white/5 bg-black/20 p-3">
        <legend className="px-1 text-[11px] font-semibold text-slate-300">Send weather events to a service</legend>
        <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {EVENTS.map((ev) => (
            <label key={ev} className="flex min-h-11 items-center gap-2 text-xs text-slate-300">
              <input type="checkbox" checked={events.includes(ev)} onChange={() => setEvents((l) => toggle(l, ev))} className="h-4 w-4 accent-sky-500" />
              {WEATHER_EVENTS[ev]}
            </label>
          ))}
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[180px_minmax(0,1fr)_auto]">
          <select
            value={service || services[0] || ''}
            onChange={(e) => setService(e.target.value)}
            aria-label="Service to send to"
            disabled={!services.length}
            className="glass-input bg-black/30 disabled:opacity-50"
          >
            {!services.length && <option value="">No services listed</option>}
            {services.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/api/hooks/weather"
            aria-label="Path on that service"
            autoCapitalize="none"
            className="glass-input bg-black/30"
          />
          <button
            type="button"
            onClick={addHook}
            disabled={saveHookList.isPending || !services.length}
            className="glass-button px-4 py-2 text-[10px] font-black uppercase tracking-widest disabled:opacity-40 pointer-coarse:min-h-11"
          >
            Connect
          </button>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400">
          <span>Only for:</span>
          {KINDS.map((k) => (
            <label key={k} className="flex min-h-8 items-center gap-1">
              <input type="checkbox" checked={kinds.includes(k)} onChange={() => setKinds((l) => toggle(l, k))} className="h-3.5 w-3.5 accent-sky-500" />
              {WEATHER_KINDS[k].label}
            </label>
          ))}
          <span className="text-slate-500">(none: all)</span>
        </div>
        <ul className="mt-3 space-y-2">
          {hooks.map((h) => (
            <li key={h.id} className="flex items-center justify-between gap-3 rounded-lg border border-white/5 bg-black/20 p-2">
              <span className="min-w-0 text-xs text-slate-300">
                <span className="font-mono text-white">{h.service}{h.path}</span>
                <span className="block text-slate-500">
                  {h.events.map((e) => WEATHER_EVENTS[e]).join(', ')}
                  {h.filter?.kinds?.length ? ` · ${h.filter.kinds.join(', ')}` : ''}
                </span>
              </span>
              <button
                type="button"
                onClick={() => remove(`${h.service}${h.path}`, () => saveHookList.mutate(hooks.filter((x) => x.id !== h.id), { onSuccess: () => done('Hook removed') }))}
                className="shrink-0 rounded-xl p-2 text-slate-400 hover:bg-red-500/10 hover:text-red-300 pointer-coarse:min-h-11"
                aria-label={`Remove hook ${h.service}${h.path}`}
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
        {info.data?.stream && (
          <p className="mt-3 text-[11px] text-slate-500">
            Services can also read every event from the Redis stream <span className="font-mono">{info.data.stream}</span>.
          </p>
        )}
      </fieldset>
    </section>
  );
};

export default WeatherIntegrationsPanel;
