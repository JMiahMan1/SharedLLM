import { useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BellRing, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import type { UserProfile } from '../../types/api';
import EntitySearchDropdown from '../ui/EntitySearchDropdown';
import HelpTooltip from '../ui/HelpTooltip';
import {
  HOME_ALERT_EVENTS,
  loadHomeAlerts,
  removeHomeAlert,
  saveHomeAlerts,
  upsertHomeAlert,
  type HomeAlert,
  type HomeAlertEvent,
} from '../../lib/homeAlerts';

/**
 * Admin mapping of Home Assistant entities to home notices (doorbell, motion
 * while away, garage left open, door unlocked at night, washer or dryer done,
 * water leak). Nothing is guessed from entity names: only entities listed
 * here send anything, and each goes to everyone or to the people picked.
 */

const EVENTS = Object.keys(HOME_ALERT_EVENTS) as HomeAlertEvent[];

export const HomeAlertsPanel: FC<{ users: UserProfile[] }> = ({ users }) => {
  const queryClient = useQueryClient();
  const [entityId, setEntityId] = useState('');
  const [event, setEvent] = useState<HomeAlertEvent>('doorbell');
  const [people, setPeople] = useState<string[]>([]);
  const [minutes, setMinutes] = useState('');
  const [watts, setWatts] = useState('');

  const { data: alerts = [], isPending, error } = useQuery<HomeAlert[]>({
    queryKey: ['home-alerts'],
    queryFn: loadHomeAlerts,
  });

  const family = users.filter((u) => !u.is_system_default);
  const info = HOME_ALERT_EVENTS[event];

  const saveMutation = useMutation({
    mutationFn: (next: HomeAlert[]) => saveHomeAlerts(next),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['home-alerts'] });
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Failed to save home alerts'),
  });

  const reset = () => {
    setEntityId('');
    setPeople([]);
    setMinutes('');
    setWatts('');
  };

  const add = () => {
    if (!entityId.trim().includes('.')) {
      toast.error('Choose a Home Assistant entity');
      return;
    }
    const next = upsertHomeAlert(alerts, {
      entity_id: entityId,
      event,
      people,
      minutes: minutes ? Number(minutes) : undefined,
      watts: watts ? Number(watts) : undefined,
    });
    saveMutation.mutate(next, {
      onSuccess: () => {
        toast.success(`${entityId.trim()} now sends "${info.label}"`);
        reset();
      },
    });
  };

  const edit = (alert: HomeAlert) => {
    setEntityId(alert.entity_id);
    setEvent(alert.event);
    setPeople(alert.people);
    setMinutes(alert.minutes ? String(alert.minutes) : '');
    setWatts(alert.watts ? String(alert.watts) : '');
  };

  const togglePerson = (username: string) =>
    setPeople((prev) => (prev.includes(username) ? prev.filter((p) => p !== username) : [...prev, username]));

  return (
    <section className="glass-panel p-4 sm:p-6 min-w-0 overflow-hidden" data-testid="home-alerts">
      <div className="mb-6 flex items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-3 text-xl font-bold text-white">
            <BellRing size={20} className="text-sky-300" />
            Home alerts
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            Pick the entities that send home notices to phones, and who hears about each.
          </p>
        </div>
        <HelpTooltip docName="INTEGRATIONS.md" sectionTitle="## Home alerts" label="Home alerts" />
      </div>

      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_240px_auto]">
        <EntitySearchDropdown
          value={entityId}
          onChange={setEntityId}
          placeholder="Search Home Assistant entities..."
          testId="home-alert-entity-search"
        />
        <select
          value={event}
          aria-label="Home alert event"
          onChange={(e) => setEvent(e.target.value as HomeAlertEvent)}
          className="glass-input bg-black/30"
        >
          {EVENTS.map((key) => (
            <option key={key} value={key}>
              {HOME_ALERT_EVENTS[key].label}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={!entityId || saveMutation.isPending}
          onClick={add}
          className="glass-button px-4 py-3 text-[10px] font-black uppercase tracking-widest disabled:opacity-40 pointer-coarse:min-h-11"
        >
          Save alert
        </button>
      </div>

      <p className="mb-3 text-[11px] text-slate-500">{info.hint}</p>

      {(info.minutes !== undefined || info.watts !== undefined) && (
        <div className="mb-4 flex flex-wrap gap-3">
          {info.minutes !== undefined && (
            <label className="flex items-center gap-2 text-xs text-slate-400">
              Minutes
              <input
                type="number"
                min={1}
                value={minutes}
                placeholder={String(info.minutes)}
                onChange={(e) => setMinutes(e.target.value)}
                aria-label="Alert minutes"
                className="glass-input w-24 bg-black/30"
              />
            </label>
          )}
          {info.watts !== undefined && (
            <label className="flex items-center gap-2 text-xs text-slate-400">
              Watts
              <input
                type="number"
                min={1}
                value={watts}
                placeholder={String(info.watts)}
                onChange={(e) => setWatts(e.target.value)}
                aria-label="Alert watts"
                className="glass-input w-24 bg-black/30"
              />
            </label>
          )}
        </div>
      )}

      <fieldset className="mb-4 rounded-xl border border-white/5 bg-black/20 p-3">
        <legend className="px-1 text-[11px] font-semibold text-slate-300">
          Who is notified {people.length === 0 && <span className="text-slate-500">(nobody picked: everyone)</span>}
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
                    checked={people.includes(name)}
                    onChange={() => togglePerson(name)}
                    aria-label={`Notify @${user.username}`}
                    className="h-4 w-4 accent-sky-500"
                  />
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>

      <div className="max-h-[28rem] space-y-3 overflow-y-auto pr-2">
        {isPending && <p className="text-xs text-slate-400">Loading home alerts…</p>}
        {error && (
          <p className="text-xs text-red-300">
            Could not load home alerts: {error instanceof Error ? error.message : 'unknown error'}
          </p>
        )}
        {!isPending && !error && alerts.length === 0 && (
          <p className="text-xs text-slate-500">No entities send home alerts yet.</p>
        )}
        {alerts.map((alert) => (
          <div key={`${alert.entity_id}:${alert.event}`} className="glass-card flex flex-wrap items-center justify-between gap-3 p-4">
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-sm text-white">{alert.entity_id}</p>
              <p className="mt-1 text-xs text-slate-400">
                {HOME_ALERT_EVENTS[alert.event].label}
                {alert.minutes ? ` · ${alert.minutes} min` : ''}
                {alert.watts ? ` · ${alert.watts} W` : ''}
                {' · '}
                {alert.people.length ? alert.people.map((p) => `@${p}`).join(', ') : 'everyone'}
              </p>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => edit(alert)}
                className="glass-button px-3 py-2 text-[10px] font-black uppercase tracking-widest pointer-coarse:min-h-11"
              >
                Edit
              </button>
              <button
                type="button"
                disabled={saveMutation.isPending}
                onClick={() => saveMutation.mutate(removeHomeAlert(alerts, alert))}
                className="rounded-xl p-2 text-slate-400 transition hover:bg-red-500/10 hover:text-red-300 disabled:opacity-40 pointer-coarse:min-h-11"
                aria-label={`Remove ${alert.entity_id} ${HOME_ALERT_EVENTS[alert.event].label}`}
              >
                <Trash2 size={16} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
};

export default HomeAlertsPanel;
