import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, House, MapPin, Pencil, Plus, RefreshCw, Thermometer, Trash2, Zap } from 'lucide-react';
import { CameraTile, EntityControl, SensorChip } from '../components/dashboards/EntityControls';
import { MoreControlsContext } from '../components/dashboards/moreControls';
import ControlSheet from '../components/devices/controls/ControlSheet';
import { AddRoomForm, RenameRoomForm } from '../components/devices/RoomForms';
import { BTN, DANGER } from '../components/devices/setupStyles';
import Modal from '../components/ui/Modal';
import PageHeader, { HEADER_ACTION } from '../components/ui/PageHeader';
import { CardSkeletons, EmptyState, ErrorRetry } from '../components/ui/StateViews';
import HomeOverview from '../components/home/HomeOverview';
import { useAuth } from '../context/AuthContext';
import { ROOMS_KEY, useRoomActions } from '../hooks/useRoomActions';
import { roomAnchor } from '../lib/homeOverview';
import {
  failureText, getRooms, groupByDomain, groupByFloor, type HubEntity, type RoomArea,
} from '../lib/dashboards';

export const ROOMS_POLL_MS = 10_000;

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

/** Rename and remove for one room; only ever rendered for an admin. */
function RoomAdminActions({ area, onRename }: { area: RoomArea; onRename: () => void }) {
  const { remove, confirmRemove } = useRoomActions();
  return (
    <div className="flex shrink-0 gap-2" data-testid="room-admin-actions">
      <button type="button" className={`${BTN} min-w-11 justify-center`} onClick={onRename} aria-label={`Rename ${area.name}`}>
        <Pencil size={14} aria-hidden /><span className="sr-only sm:not-sr-only">Rename</span>
      </button>
      <button type="button" className={DANGER} disabled={remove.isPending} aria-label={`Remove ${area.name}`}
        onClick={() => void confirmRemove(area)}>
        <Trash2 size={14} aria-hidden />
      </button>
    </div>
  );
}

function RoomCard({ area, onChanged, isAdmin, onRename }: {
  area: RoomArea; onChanged: () => void; isAdmin: boolean; onRename: () => void;
}) {
  return (
    <article id={roomAnchor(area.area_id)} className="glass-card rounded-2xl p-4 space-y-3 scroll-mt-4" data-testid="room-card" aria-label={area.name}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="min-w-0 break-words pt-2 text-base font-semibold text-slate-100">{area.name}</h3>
        {isAdmin && <RoomAdminActions area={area} onRename={onRename} />}
      </div>
      {area.entities.length > 0 ? <RoomBody entities={area.entities} onChanged={onChanged} /> : (
        <p className="text-sm text-slate-400" data-testid="room-empty">
          Nothing in this room yet. Put devices in it from{' '}
          <Link to="/devices" className="text-amber-300 underline underline-offset-2">Devices</Link>.
        </p>
      )}
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
  // Admins only: the Add room dialog, and the room being renamed.
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState<RoomArea | null>(null);
  // A name tapped on a room card opens that entity's full control, with its device's other entities.
  const [more, setMore] = useState<string | null>(null);
  const everything = data ? [...data.areas.flatMap((a) => a.entities), ...data.unassigned] : [];
  const moreEntity = everything.find((e) => e.entity_id === more) ?? null;
  // A control's change shows up on the next poll; ask a little sooner than that.
  const onChanged = () => window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: ROOMS_KEY }), 1500);
  const empty = data && data.areas.every((a) => a.entities.length === 0) && data.unassigned.length === 0;
  // An admin sees every room, even one with nothing in it yet, so a room just added shows at once.
  const noRooms = isAdmin ? Boolean(data && data.areas.length === 0) : empty;

  return (
    <MoreControlsContext.Provider value={(e) => setMore(e.entity_id)}>
    <div className="mx-auto w-full max-w-7xl space-y-4 pb-24" data-testid="rooms-page">
      <PageHeader
        icon={House}
        tone="text-amber-400"
        title="Rooms"
        subtitle="Lights, climate, cameras and energy, room by room."
        actions={(
          <nav className="flex flex-wrap gap-2" aria-label="Related pages">
            {isAdmin && (
              <button type="button" onClick={() => setAdding(true)} className={HEADER_ACTION} data-testid="add-room">
                <Plus size={14} aria-hidden />Add room
              </button>
            )}
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

      {data && groupByFloor(data, isAdmin).map(({ floor, areas }) => (
        <section key={floor?.floor_id ?? 'rooms'} className="space-y-3" aria-label={floor?.name ?? 'Rooms'}>
          {floor && <h2 className="text-sm font-black uppercase tracking-widest text-slate-500">{floor.name ?? 'Floor'}</h2>}
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {areas.map((a) => (
              <RoomCard key={a.area_id} area={a} onChanged={onChanged} isAdmin={isAdmin} onRename={() => setRenaming(a)} />
            ))}
          </div>
        </section>
      ))}

      {noRooms && (
        <EmptyState
          icon={<House size={28} />}
          title="No rooms yet"
          detail={isAdmin
            ? 'Add the rooms in your home, like Kitchen or Living room. Then put devices in them from Devices.'
            : 'Devices show up here once a parent puts them in a room you can see.'}
          action={isAdmin ? { label: 'Add room', onClick: () => setAdding(true) } : null}
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
      {isAdmin && (
        <Modal isOpen={adding} onClose={() => setAdding(false)} title="Add a room" size="md">
          <p className="mb-3 text-sm text-slate-400">Give it a name everyone in the house uses. You can pick its floor too.</p>
          <AddRoomForm floors={data?.floors ?? []} onAdded={() => setAdding(false)} />
        </Modal>
      )}
      {isAdmin && renaming && (
        <Modal isOpen onClose={() => setRenaming(null)} title={`Rename ${renaming.name}`} size="md">
          <RenameRoomForm room={renaming} onDone={() => setRenaming(null)} />
        </Modal>
      )}
      {moreEntity && (
        <ControlSheet entity={moreEntity} onClose={() => setMore(null)}
          area={data?.areas.find((a) => a.area_id === moreEntity.area_id)?.name}
          siblings={moreEntity.device_id ? everything.filter((x) => x.device_id === moreEntity.device_id) : []} />
      )}
    </div>
    </MoreControlsContext.Provider>
  );
}
