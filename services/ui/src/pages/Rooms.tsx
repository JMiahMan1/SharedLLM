import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Home, Loader2, MapPin, RefreshCw, Thermometer, Zap } from 'lucide-react';
import { CameraTile, EntityControl, SensorChip } from '../components/dashboards/EntityControls';
import {
  failureText, getRooms, groupByDomain, groupByFloor, type HubEntity, type RoomArea,
} from '../lib/dashboards';

export const ROOMS_POLL_MS = 10_000;
const ROOMS_KEY = ['dashboard', 'rooms'];

/** Things to glance at (chips) rather than rows with controls. */
const CHIP_DOMAINS = new Set(['sensor']);

function RoomBody({ entities, onChanged }: { entities: HubEntity[]; onChanged: () => void }) {
  const groups = groupByDomain(entities);
  const chips = groups.filter((g) => CHIP_DOMAINS.has(g.domain)).flatMap((g) => g.entities);
  const cameras = groups.find((g) => g.domain === 'camera')?.entities ?? [];
  const rows = groups.filter((g) => !CHIP_DOMAINS.has(g.domain) && g.domain !== 'camera');
  return (
    <div className="space-y-3">
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">{chips.map((e) => <SensorChip key={e.entity_id} entity={e} />)}</div>
      )}
      {rows.map((g) => (
        <section key={g.domain} aria-label={g.label}>
          <h4 className="text-[10px] font-black uppercase tracking-widest text-slate-500">{g.label}</h4>
          <div className="divide-y divide-white/5">
            {g.entities.map((e) => <EntityControl key={e.entity_id} entity={e} onChanged={onChanged} />)}
          </div>
        </section>
      ))}
      {cameras.length > 0 && (
        <div className="grid grid-cols-2 gap-2">{cameras.map((e) => <CameraTile key={e.entity_id} entity={e} />)}</div>
      )}
    </div>
  );
}

function RoomCard({ area, onChanged }: { area: RoomArea; onChanged: () => void }) {
  return (
    <article className="glass-card rounded-2xl p-4 space-y-3" data-testid="room-card" aria-label={area.name}>
      <h3 className="text-base font-semibold text-slate-100">{area.name}</h3>
      <RoomBody entities={area.entities} onChanged={onChanged} />
    </article>
  );
}

export default function Rooms() {
  const queryClient = useQueryClient();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ROOMS_KEY, queryFn: getRooms, refetchInterval: ROOMS_POLL_MS, refetchIntervalInBackground: false,
  });
  const [otherOpen, setOtherOpen] = useState(false);
  // A control's change shows up on the next poll; ask a little sooner than that.
  const onChanged = () => window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: ROOMS_KEY }), 1500);

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-7xl mx-auto">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold flex items-center gap-2"><Home size={22} className="text-amber-400" />Rooms</h1>
        <nav className="flex flex-wrap gap-2 text-sm" aria-label="Related pages">
          <Link to="/climate" className="min-h-11 flex items-center gap-1 rounded-xl bg-white/5 px-3"><Thermometer size={14} />Climate</Link>
          <Link to="/energy" className="min-h-11 flex items-center gap-1 rounded-xl bg-white/5 px-3"><Zap size={14} />Energy</Link>
          <Link to="/wander" className="min-h-11 flex items-center gap-1 rounded-xl bg-white/5 px-3"><MapPin size={14} />People</Link>
          <button type="button" onClick={() => void refetch()} aria-label="Refresh"
            className="min-h-11 min-w-11 flex items-center justify-center rounded-xl bg-white/5">
            <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} />
          </button>
        </nav>
      </header>

      {isLoading && <p className="flex items-center gap-2 text-slate-400"><Loader2 className="animate-spin" size={16} />Loading rooms...</p>}
      {error && <p role="alert" className="text-red-400">{failureText(error, 'Could not load the rooms')}</p>}

      {data && groupByFloor(data).map(({ floor, areas }) => (
        <section key={floor?.floor_id ?? 'rooms'} className="space-y-3" aria-label={floor?.name ?? 'Rooms'}>
          {floor && <h2 className="text-sm font-black uppercase tracking-widest text-slate-500">{floor.name ?? floor.floor_id}</h2>}
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {areas.map((a) => <RoomCard key={a.area_id} area={a} onChanged={onChanged} />)}
          </div>
        </section>
      ))}

      {data && data.areas.every((a) => a.entities.length === 0) && data.unassigned.length === 0 && (
        <p className="text-slate-400">Nothing here yet: devices appear once they are assigned to an area you can see.</p>
      )}

      {data && data.unassigned.length > 0 && (
        <article className="glass-card rounded-2xl p-4">
          <button type="button" aria-expanded={otherOpen} onClick={() => setOtherOpen((o) => !o)}
            className="min-h-11 w-full flex items-center gap-2 text-left text-base font-semibold text-slate-200">
            {otherOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            Other <span className="text-xs text-slate-500">({data.unassigned.length}, not in a room)</span>
          </button>
          {otherOpen && <div className="mt-3"><RoomBody entities={data.unassigned} onChanged={onChanged} /></div>}
        </article>
      )}
    </div>
  );
}
