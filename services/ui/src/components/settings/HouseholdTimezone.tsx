import { useMemo, useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Clock } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import {
  browserTimeZone,
  filterTimeZones,
  listTimeZones,
  type HouseholdTimezone as HouseholdTimezoneState,
  type HouseholdTimezoneUpdate,
} from '../../lib/householdTimezone';

const QUERY_KEY = ['household-timezone'];

const SOURCE_LABEL: Record<string, string> = {
  'household setting': 'the household setting',
  'TZ in .env': 'TZ in the server .env',
  'home location': 'the home location',
};

function errorDetail(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return err instanceof Error && err.message ? err.message : 'Could not save the time zone';
}

/**
 * The one place to set the household time zone (admin only). Shows the zone in
 * effect and which source it came from, or the server's error when no source
 * supplies one; it never fills in a zone on its own.
 */
const HouseholdTimezone: FC = () => {
  const queryClient = useQueryClient();
  const zones = useMemo(() => listTimeZones(), []);
  const browserZone = useMemo(() => browserTimeZone(), []);
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState<string | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => api.getHouseholdTimezone(),
  });

  const save = useMutation({
    mutationFn: (update: HouseholdTimezoneUpdate) => api.setHouseholdTimezone(update),
    onSuccess: (next: HouseholdTimezoneState, update) => {
      queryClient.setQueryData(QUERY_KEY, next);
      setDraft(null);
      if (update.timezone !== undefined) {
        toast.success(update.timezone ? `Household time zone set to ${update.timezone}` : 'Household time zone cleared');
      }
    },
    onError: (err: unknown) => toast.error(errorDetail(err)),
  });

  const setting = data?.setting ?? '';
  const selected = draft ?? setting;
  const shown = useMemo(() => {
    const matches = filterTimeZones(zones, query);
    // Keep the chosen zone listed even when the search would hide it.
    return selected && !matches.includes(selected) ? [selected, ...matches] : matches;
  }, [zones, query, selected]);

  if (isLoading) {
    return <div data-testid="household-timezone" className="text-xs text-slate-500">Loading the household time zone...</div>;
  }
  if (isError || !data) {
    return (
      <div data-testid="household-timezone" role="alert" className="text-xs text-red-400">
        Could not load the household time zone: {errorDetail(error)}
      </div>
    );
  }

  const dirty = draft !== null && draft !== setting;
  const busy = save.isPending;

  return (
    <div data-testid="household-timezone" className="space-y-3">
      <div className="flex items-center gap-2">
        <Clock size={14} className="text-slate-500 shrink-0" />
        <h4 className="text-[9px] font-black uppercase tracking-widest text-slate-500">Household time zone</h4>
      </div>

      <div data-testid="household-timezone-effective" className="rounded-lg border border-white/10 bg-black/30 p-3 text-xs">
        {data.zone ? (
          <p className="text-slate-200">
            In effect: <span className="font-semibold text-white">{data.zone}</span>
            {data.source && <span className="text-slate-400"> (from {SOURCE_LABEL[data.source] ?? data.source})</span>}
          </p>
        ) : (
          <p role="alert" className="text-red-400">{data.error || 'No time zone is configured.'}</p>
        )}
        <p className="mt-1 text-[10px] text-slate-500">
          TZ in the server .env: {data.env_tz ? data.env_tz : 'not set'}. The gateway uses a change at once; other
          services pick it up when they restart.
        </p>
        {data.order?.length > 0 && (
          <p className="mt-1 text-[10px] text-slate-500">Checked in this order: {data.order.join(', then ')}.</p>
        )}
      </div>

      <div className="space-y-2">
        {zones.length > 0 ? (
          <>
            <input
              type="search"
              aria-label="Search time zones"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search, e.g. Berlin or New York"
              className="glass-input w-full min-h-11 text-xs"
            />
            <select
              aria-label="Household time zone"
              value={selected}
              onChange={(e) => setDraft(e.target.value)}
              className="glass-input w-full min-h-11 text-xs bg-black/40"
            >
              <option value="">Not set (use TZ from the server .env)</option>
              {shown.map((zone) => (
                <option key={zone} value={zone}>{zone}</option>
              ))}
            </select>
            {query && shown.length === 0 && <p className="text-[10px] text-slate-500">No zone matches "{query}".</p>}
          </>
        ) : (
          // Older browsers cannot list zones: type one; the server checks it.
          <input
            type="text"
            aria-label="Household time zone"
            value={selected}
            onChange={(e) => setDraft(e.target.value.trim())}
            placeholder="IANA zone, e.g. Europe/Berlin"
            className="glass-input w-full min-h-11 text-xs"
          />
        )}

        <div className="flex flex-wrap gap-2">
          {browserZone && (
            <button
              type="button"
              onClick={() => setDraft(browserZone)}
              disabled={busy}
              className="min-h-11 rounded-lg border border-white/10 bg-white/5 px-3 text-xs text-slate-200 hover:bg-white/10 disabled:opacity-50"
            >
              Use my browser's zone ({browserZone})
            </button>
          )}
          <button
            type="button"
            onClick={() => save.mutate({ timezone: selected })}
            disabled={busy || !dirty}
            className="min-h-11 rounded-lg bg-blue-600 px-3 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
          >
            Save time zone
          </button>
          <button
            type="button"
            onClick={() => save.mutate({ timezone: '' })}
            disabled={busy || !setting}
            className="min-h-11 rounded-lg border border-white/10 bg-white/5 px-3 text-xs text-slate-200 hover:bg-white/10 disabled:opacity-50"
          >
            Clear (use TZ from .env)
          </button>
        </div>
      </div>

      <label className="flex min-h-11 items-center gap-3 text-xs text-slate-300">
        <input
          type="checkbox"
          role="switch"
          aria-checked={data.from_location}
          checked={data.from_location}
          disabled={busy}
          onChange={(e) => save.mutate({ from_location: e.target.checked })}
          className="h-5 w-5 shrink-0"
        />
        <span>
          Derive from home location
          <span className="block text-[10px] text-slate-500">
            {data.location_set
              ? 'Used only when neither the household setting nor TZ in .env gives a zone.'
              : 'No home location is set yet, so this cannot supply a zone.'}
          </span>
        </span>
      </label>
    </div>
  );
};

export default HouseholdTimezone;
