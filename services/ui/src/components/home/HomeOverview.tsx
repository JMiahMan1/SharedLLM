import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Cctv, Lightbulb, Thermometer, Zap } from 'lucide-react';
import ScheduleStatus from '../schedules/ScheduleStatus';
import { HEADER_ACTION } from '../ui/PageHeader';
import { formatKwh, formatMoney, formatPower, getEnergySummary, runService, type RoomsResponse } from '../../lib/dashboards';
import { formatTemp } from '../../lib/climate';
import { getClimate as getScheduleOverview } from '../../lib/schedules';
import { camerasIn, lightsOnByRoom, lightsSummary, roomAnchor } from '../../lib/homeOverview';

const TILE = 'glass-card rounded-2xl p-4 space-y-2 min-w-0';
const TILE_TITLE = 'flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-400';

/**
 * The top of the Rooms page: the controls a family reaches for most, in one
 * screen. Lights on by room (with one tap to turn a room off), each
 * thermostat with what its schedule does next and Hold / Resume, the cameras,
 * and energy used today. Each tile hides itself when its part of the hub has
 * nothing to show, so a house without cameras never sees an empty camera tile.
 */
export default function HomeOverview({ rooms, onChanged }: { rooms: RoomsResponse; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const climate = useQuery({ queryKey: ['homehub-climate'], queryFn: getScheduleOverview, retry: false,
    refetchInterval: () => (document.hidden ? false : 30_000) });
  const today = new Date();
  const energy = useQuery({ queryKey: ['dashboard', 'energy', 'day', today.toDateString()],
    queryFn: () => getEnergySummary('day', today), retry: false, refetchInterval: 60_000 });

  // Rooms switched off here, shown off at once; put back if the hub refuses.
  const [turnedOff, setTurnedOff] = useState<Set<string>>(() => new Set());
  const groups = lightsOnByRoom(rooms).filter((g) => !turnedOff.has(g.area_id));
  const cameras = camerasIn(rooms);
  const thermostats = climate.data?.thermostats ?? [];
  const energyData = energy.data;
  const hasEnergy = Boolean(energyData && (energyData.grid.stats.length || energyData.utility.length));

  async function roomOff(areaId: string, ids: string[]) {
    setTurnedOff((s) => new Set(s).add(areaId));
    const results = await Promise.all(ids.map((id) => runService('light', 'turn_off', id)));
    if (results.some((ok) => !ok)) {
      setTurnedOff((s) => {
        const next = new Set(s);
        next.delete(areaId);
        return next;
      });
    }
    onChanged();
  }

  const onScheduleChanged = () => void queryClient.invalidateQueries({ queryKey: ['homehub-climate'] });

  return (
    <section aria-label="At a glance" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="home-overview">
      <div className={TILE} data-testid="glance-lights">
        <h2 className={TILE_TITLE}><Lightbulb size={14} className="text-amber-300" aria-hidden />Lights</h2>
        <p className="text-sm text-slate-200">{lightsSummary(groups)}</p>
        {groups.length > 0 && (
          <ul className="space-y-1.5">
            {groups.map((g) => (
              <li key={g.area_id || 'other'} className="flex items-center justify-between gap-2">
                <a href={`#${roomAnchor(g.area_id)}`} className="min-h-11 flex min-w-0 items-center text-sm text-slate-300 hover:text-white">
                  <span className="truncate">{g.name}</span>
                  <span className="ml-1.5 shrink-0 text-xs text-slate-500">{g.on.length} of {g.total}</span>
                </a>
                <button type="button" className={HEADER_ACTION} aria-label={`Turn off the lights in ${g.name}`}
                  onClick={() => void roomOff(g.area_id, g.on.map((e) => e.entity_id))}>
                  Turn off
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {thermostats.length > 0 && (
        <div className={`${TILE} sm:col-span-2 xl:col-span-2`} data-testid="glance-climate">
          <h2 className={TILE_TITLE}><Thermometer size={14} className="text-cyan-400" aria-hidden />Climate</h2>
          <ul className="grid gap-3 md:grid-cols-2">
            {thermostats.map((t) => (
              <li key={t.entity_id} className="min-w-0 rounded-xl border border-white/10 p-3 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <Link to="/climate" className="min-h-11 inline-flex min-w-0 items-center text-sm font-semibold text-white hover:underline">
                    <span className="truncate">{t.name}</span>
                  </Link>
                  {typeof t.current_temperature === 'number' && (
                    <span className="shrink-0 text-lg font-bold tabular-nums text-white">{formatTemp(t.current_temperature)}°</span>
                  )}
                </div>
                {t.area && <p className="text-xs text-slate-500">{t.area}</p>}
                <ScheduleStatus thermostat={t} onChanged={onScheduleChanged} compact />
              </li>
            ))}
          </ul>
        </div>
      )}

      {cameras.length > 0 && (
        <Link to="/cameras" className={`${TILE} block hover:border-cyan-500/30`} data-testid="glance-cameras">
          <h2 className={TILE_TITLE}><Cctv size={14} className="text-cyan-400" aria-hidden />Cameras</h2>
          <p className="text-sm text-slate-200">{cameras.length} camera{cameras.length === 1 ? '' : 's'}</p>
          <p className="text-xs text-slate-500">Watch live or see today's recordings</p>
        </Link>
      )}

      {hasEnergy && energyData && (
        <Link to="/energy" className={`${TILE} block hover:border-amber-500/30`} data-testid="glance-energy">
          <h2 className={TILE_TITLE}><Zap size={14} className="text-amber-400" aria-hidden />Energy today</h2>
          <p className="text-xl font-bold tabular-nums text-amber-300">{formatKwh(energyData.grid.kwh)}</p>
          <p className="text-xs text-slate-400">
            {energyData.grid.cost != null ? `${formatMoney(energyData.grid.cost, energyData.currency)} so far` : 'No rate set'}
            {energyData.grid.power_w != null ? ` · ${formatPower(energyData.grid.power_w)} now` : ''}
          </p>
        </Link>
      )}
    </section>
  );
}
