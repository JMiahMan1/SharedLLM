import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CloudSun } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { parseLocation, type MyWeatherPrefs, type WeatherLocation, type WeatherUnits } from '../../lib/weather';
import type { SavedPlace } from '../../types/api';

/**
 * Settings > Weather: where *your* forecast is for (docs/WEATHER.md).
 *
 * The admin sets the household's location and units; anyone can keep that
 * default, follow where their phone is (the location it already shares with
 * the app), or choose their own place (typed in, this device's location, or
 * one of Wander's saved places), and pick their own units. It changes the
 * dashboard widget, calendar and calendar reminder notes for this person
 * only; household reminders keep the places the admin gave them.
 */

const PREFS_KEY = ['weather', 'mine'];

type Choice = 'household' | 'follow' | 'own';

const CHOICES: ReadonlyArray<readonly [Choice, string]> = [
  ['household', 'Household default'],
  ['follow', 'Follow my phone'],
  ['own', 'A place I choose'],
];

function OwnPlaceForm({ initial, places, saving, onSave }: {
  initial: WeatherLocation | null;
  places: SavedPlace[];
  saving: boolean;
  onSave: (loc: WeatherLocation) => void;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [lat, setLat] = useState(initial ? String(initial.latitude) : '');
  const [lon, setLon] = useState(initial ? String(initial.longitude) : '');

  const fromDevice = () => {
    if (!navigator.geolocation) {
      toast.error('This device cannot share its location');
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLat(pos.coords.latitude.toFixed(4));
        setLon(pos.coords.longitude.toFixed(4));
        if (!name) setName('My place');
      },
      () => toast.error('Could not read this device\'s location'),
      { timeout: 10000 },
    );
  };

  const pick = (id: string) => {
    const place = places.find((p) => p.id === id);
    if (!place) return;
    setName(place.name);
    setLat(String(place.lat));
    setLon(String(place.lon));
  };

  const save = () => {
    const loc = parseLocation({ name, latitude: lat, longitude: lon });
    if (!loc) {
      toast.error('Latitude must be -90 to 90 and longitude -180 to 180');
      return;
    }
    onSave(loc);
  };

  return (
    <div className="space-y-3 rounded-xl border border-white/5 bg-black/20 p-3">
      {places.length > 0 && (
        <select
          defaultValue=""
          onChange={(e) => pick(e.target.value)}
          aria-label="Use a saved place"
          className="glass-input w-full bg-black/30"
        >
          <option value="" disabled>Use a saved place…</option>
          {places.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      )}
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Name, e.g. Grandma's"
        aria-label="My weather place name"
        maxLength={60}
        className="glass-input w-full bg-black/30"
      />
      <div className="grid grid-cols-2 gap-2">
        <input value={lat} onChange={(e) => setLat(e.target.value)} inputMode="decimal" placeholder="Latitude"
          aria-label="My weather latitude" className="glass-input min-w-0 bg-black/30" />
        <input value={lon} onChange={(e) => setLon(e.target.value)} inputMode="decimal" placeholder="Longitude"
          aria-label="My weather longitude" className="glass-input min-w-0 bg-black/30" />
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={fromDevice}
          className="min-h-11 flex-1 rounded-xl bg-white/5 px-3 text-sm text-slate-300 hover:bg-white/10">
          Use where I am
        </button>
        <button type="button" onClick={save} disabled={saving}
          className="min-h-11 flex-1 rounded-xl border border-[var(--site-accent-border)] bg-[var(--site-accent-soft)] px-3 text-sm font-medium text-[var(--site-accent-text)] disabled:opacity-50">
          Save my place
        </button>
      </div>
    </div>
  );
}

export default function WeatherSettingsPanel() {
  const queryClient = useQueryClient();
  const prefs = useQuery<MyWeatherPrefs>({ queryKey: PREFS_KEY, queryFn: api.getMyWeather, retry: false });
  const places = useQuery<SavedPlace[]>({ queryKey: ['saved-places'], queryFn: api.getSavedPlaces, retry: false });
  const [choice, setChoice] = useState<Choice | null>(null);

  const save = useMutation({
    mutationFn: (next: Partial<MyWeatherPrefs>) => api.setMyWeather(next),
    onSuccess: (data) => {
      queryClient.setQueryData(PREFS_KEY, data);
      queryClient.invalidateQueries({ queryKey: ['weather'] });
      toast.success('Weather updated');
    },
    onError: (err: unknown) => toast.error(err instanceof Error ? err.message : 'Could not save'),
  });

  const current: Choice = choice ?? (prefs.data?.follow ? 'follow' : prefs.data?.location ? 'own' : 'household');
  const units = prefs.data?.units ?? 'household';

  const choose = (next: Choice) => {
    setChoice(next);
    const data = prefs.data;
    if (next === 'household' && (data?.location || data?.follow)) save.mutate({ location: null, follow: false });
    if (next === 'follow' && !data?.follow) save.mutate({ follow: true });
    if (next === 'own' && data?.follow) save.mutate({ follow: false });
  };

  return (
    <section className="glass-panel space-y-3 rounded-2xl p-4" aria-labelledby="weather-settings-heading" data-testid="weather-settings">
      <div className="px-1">
        <h2 id="weather-settings-heading" className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-slate-400">
          <CloudSun size={16} className="text-sky-300" />
          Weather
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          The place your dashboard and calendar forecast is for. Household reminders keep the places they were set for.
        </p>
      </div>

      {prefs.isError ? (
        <p className="px-1 text-xs text-slate-500" role="status">Weather settings are unavailable right now.</p>
      ) : (
        <>
          <div role="radiogroup" aria-label="Forecast place" className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {CHOICES.map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={current === value}
                onClick={() => choose(value)}
                disabled={prefs.isPending}
                className={`min-h-11 rounded-xl px-3 text-sm font-medium transition-colors ${
                  current === value
                    ? 'border border-[var(--site-accent-border)] bg-[var(--site-accent-soft)] text-[var(--site-accent-text)]'
                    : 'bg-white/5 text-slate-300 hover:bg-white/10'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {current === 'follow' && prefs.data && (
            <p className="px-1 text-xs text-slate-400" role="status" data-testid="weather-follow-status">
              {prefs.data.follow && prefs.data.live
                ? 'Using where your phone last shared its location.'
                : `Your phone has not shared its location in the last few hours, or sharing is paused, so this uses ${
                  prefs.data.location?.name ? prefs.data.location.name : 'the household forecast'} until it does.`}
            </p>
          )}

          {current === 'own' && prefs.data && (
            <OwnPlaceForm
              key={JSON.stringify(prefs.data.location)}
              initial={prefs.data.location}
              places={places.data ?? []}
              saving={save.isPending}
              onSave={(location) => save.mutate({ location })}
            />
          )}

          <label className="flex items-center justify-between gap-3 px-1 text-sm text-slate-300">
            Units
            <select
              value={units}
              onChange={(e) => save.mutate({ units: e.target.value === 'household' ? null : (e.target.value as WeatherUnits) })}
              aria-label="My weather units"
              className="glass-input bg-black/30"
            >
              <option value="household">Household default</option>
              <option value="imperial">°F, mph, inches</option>
              <option value="metric">°C, km/h, mm</option>
            </select>
          </label>
        </>
      )}
    </section>
  );
}
