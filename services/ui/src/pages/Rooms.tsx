import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, House, MapPin, RefreshCw, Thermometer, Zap } from 'lucide-react';
import { CameraTile, EntityControl, SensorChip } from '../components/dashboards/EntityControls';
import PageHeader, { HEADER_ACTION } from '../components/ui/PageHeader';
import { CardSkeletons, EmptyState, ErrorRetry } from '../components/ui/StateViews';
import HomeOverview from '../components/home/HomeOverview';
import { useAuth } from '../context/AuthContext';
import { roomAnchor } from '../lib/homeOverview';
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
    <article id={roomAnchor(area.area_id)} className="glass-card rounded-2xl p-4 space-y-3 scroll-mt-4" data-testid="room-card" aria-label={area.name}>
      <h3 className="text-base font-semibold text-slate-100">{area.name}</h3>
      <RoomBody entities={area.entities} onChanged={onChanged} />
    </article>
  );
}

export default function Rooms() {
  const queryClient = useQueryClient();
  const isAdmin = Boolean(useAuth().user?.is_admin);
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ROOMS_KEY, queryFn: getRooms, refetchInterval: ROOMS_POLL_MS, refetchIntervalInBackground: false,
  });
  const [otherOpen, setOtherOpen] = useState(false);
  // A control's change shows up on the next poll; ask a little sooner than that.
  const onChanged = () => window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: ROOMS_KEY }), 1500);
  const empty = data && data.areas.every((a) => a.entities.length === 0) && data.unassigned.length === 0;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-4 pb-24" data-testid="rooms-page">
      <PageHeader
        icon={House}
        tone="text-amber-400"
        title="Rooms"
        subtitle="Lights, climate, cameras and energy, room by room."
        actions={(
          <nav className="flex flex-wrap gap-2" aria-label="Related pages">
            <Link to="/climate" className={HEADER_ACTION}><Thermometer size={14} aria-hidden />Climate</Link>
            <Link to="/energy" className={HEADER_ACTION}><Zap size={14} aria-hidden />Energy</Link>
            <Link to="/wander" className={HEADER_ACTION}><MapPin size={14} aria-hidden />People</Link>
            <button type="button" onClick={() => void refetch()} aria-label="Refresh" className={HEADER_ACTION}>
              <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} aria-hidden />
            </button>
          </nav>
        )}
      />

      {isLoading && <CardSkeletons count={6} label="Loading rooms" />}
      {error && <ErrorRetry title={failureText(error, 'Could not load the rooms')} onRetry={() => void refetch()} />}

      {data && !empty && <HomeOverview rooms={data} onChanged={onChanged} />}

      {data && groupByFloor(data).map(({ floor, areas }) => (
        <section key={floor?.floor_id ?? 'rooms'} className="space-y-3" aria-label={floor?.name ?? 'Rooms'}>
          {floor && <h2 className="text-sm font-black uppercase tracking-widest text-slate-500">{floor.name ?? 'Floor'}</h2>}
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {areas.map((a) => <RoomCard key={a.area_id} area={a} onChanged={onChanged} />)}
          </div>
        </section>
      ))}

      {empty && (
        <EmptyState
          icon={<House size={28} />}
          title="No rooms yet"
          detail={isAdmin
            ? 'Add devices in Devices and put each one in a room; they show up here.'
            : 'Devices show up here once a parent puts them in a room you can see.'}
          action={isAdmin ? { label: 'Go to Devices', to: '/devices' } : null}
        />
      )}

      {data && data.unassigned.length > 0 && (
        <article id={roomAnchor('')} className="glass-card rounded-2xl p-4 scroll-mt-4">
          <button type="button" aria-expanded={otherOpen} onClick={() => setOtherOpen((o) => !o)}
            className="min-h-11 w-full flex items-center gap-2 text-left text-base font-semibold text-slate-200">
            {otherOpen ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
            Other <span className="text-xs text-slate-500">({data.unassigned.length}, not in a room)</span>
          </button>
          {otherOpen && <div className="mt-3"><RoomBody entities={data.unassigned} onChanged={onChanged} /></div>}
        </article>
      )}
    </div>
  );
}
