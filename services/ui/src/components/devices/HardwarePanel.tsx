import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ChevronRight, Eye, Plus, Search, X } from 'lucide-react';
import DeviceTile from './DeviceTile';
import HubDeviceSheet from './HubDeviceSheet';
import { getRooms, type HubEntity } from '../../lib/dashboards';
import { failureText, updateHubDevice } from '../../lib/discoveredDevices';
import { setDeviceHidden } from '../../lib/duplicates';
import {
  STATUS_LABEL, filterDevices, getHardware, hiddenDevices, needsAttention, physicalDevices, type HardwareDevice,
  type PhysicalDevice,
} from '../../lib/hardware';
import { TYPE_FILTERS, primaryEntity, tileKind, typeFilterOf, type TileKind } from '../../lib/deviceTiles';

/*
 * Design note (Devices > Your devices, November 2026).
 *
 * Looked at: Home Assistant (Settings > Devices: a data table with search, filters and group-by
 * area; the device page with Controls / Sensors / Configuration / Diagnostic cards; the tile
 * card; the more-info dialog with its big brightness slider, colour temperature and colour
 * wheel), Google Home (rooms of square tiles, one tap toggles, the tile tinted by the light's
 * colour, a long press opens a panel with one big slider), Apple Home (tiles grouped by room,
 * plain status lines like "Off" or "21°", accessory details behind a settings cog) and Homey
 * (type filters across the top).
 *
 * Taken: rooms of tiles, two across on a phone and more on wider screens (Google and Apple);
 * one plain state line and a one-tap toggle per tile, a lit light tinted with its own colour
 * (Google); Home Assistant's split of a device's entities into Controls, Readings, Configuration
 * and Diagnostics; its big sliders; type chips in one row (Homey).
 *
 * Done differently from Home Assistant, where it is clumsy for a family: the device page leads
 * with the device's control, not with its integration and configuration; entity ids, MAC and IP
 * live under a folded "Device info", never on a tile; entity names drop the device's name ("Default
 * fade", not "Kauf Bulb Default fade"); offline devices say how long they have been offline and
 * their controls are disabled with the reason, instead of a greyed "Unavailable"; a command the
 * hub refuses is drawn back and its own message shown, never a success it did not get.
 */

const HARDWARE_KEY = ['homehub-hardware'];
const STATES_KEY = ['dashboard', 'rooms'];

type GroupMode = 'area' | 'type' | 'integration';

const CHIP = 'inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-full border px-3.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-sky-400';
const CHIP_OFF = `${CHIP} border-white/10 bg-white/[0.03] text-slate-300 hover:bg-white/[0.07]`;
const CHIP_ON = `${CHIP} border-sky-400/50 bg-sky-500/15 text-sky-100`;
const SEG = 'min-h-11 flex-1 rounded-lg px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-sky-400 md:flex-none';

interface Row {
  device: PhysicalDevice;
  entities: HubEntity[];
  kind: TileKind;
  type: string;
}

/** Every room's live entities by device, from the Rooms page's own feed (one request for the grid). */
function statesByDevice(rooms: Awaited<ReturnType<typeof getRooms>> | undefined): Map<string, HubEntity[]> {
  const out = new Map<string, HubEntity[]>();
  if (!rooms) return out;
  for (const e of [...rooms.areas.flatMap((a) => a.entities), ...rooms.unassigned]) {
    if (!e.device_id) continue;
    out.set(e.device_id, [...(out.get(e.device_id) ?? []), e]);
  }
  return out;
}

/** Devices in no room, each with a room picker, so a whole house can be sorted in a few minutes. */
function NoRoom({ devices, areas }: { devices: PhysicalDevice[]; areas: { area_id: string; name: string }[] }) {
  const qc = useQueryClient();
  const move = useMutation({
    // Every integration's view of the device moves together, so they never disagree on the room.
    mutationFn: async ({ device, area }: { device: PhysicalDevice; area: string }) => {
      for (const v of device.views) await updateHubDevice(v.id, { area_id: area });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: HARDWARE_KEY }),
  });
  if (!devices.length) return null;
  return (
    <section className="rounded-2xl border border-amber-400/25 bg-amber-500/[0.06] p-3 sm:p-4" data-testid="no-room-list"
      aria-label="Devices in no room">
      <h3 className="text-sm font-semibold text-amber-100">Put these in a room</h3>
      <p className="mt-0.5 text-xs text-slate-400">A device in a room can be found by room, and voice can say &quot;the kitchen lights&quot;.</p>
      <ul className="mt-2 divide-y divide-white/5">
        {devices.map((d) => (
          <li key={d.id} className="flex min-w-0 flex-wrap items-center justify-between gap-2 py-2">
            <span className="min-w-0 flex-1 truncate text-sm text-slate-200" title={d.name}>{d.name}</span>
            <select className="min-h-11 min-w-0 rounded-xl border border-white/10 bg-black/30 px-2 text-sm text-white"
              defaultValue="" aria-label={`Room for ${d.name}`} disabled={move.isPending}
              onChange={(e) => e.target.value && move.mutate({ device: d, area: e.target.value })}>
              <option value="" disabled>Choose a room</option>
              {areas.map((a) => <option key={a.area_id} value={a.area_id}>{a.name}</option>)}
            </select>
          </li>
        ))}
      </ul>
      {move.isError && <p className="mt-1 text-xs text-amber-300/90" role="alert">{failureText(move.error)}</p>}
    </section>
  );
}

/** Devices a person hid (a duplicate's other copy), folded at the end, each with Show. They still work. */
function HiddenDevices({ devices }: { devices: HardwareDevice[] }) {
  const qc = useQueryClient();
  const show = useMutation({
    mutationFn: (id: string) => setDeviceHidden(id, false),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: HARDWARE_KEY });
      void qc.invalidateQueries({ queryKey: ['homehub-duplicates'] });
      void qc.invalidateQueries({ queryKey: STATES_KEY });
    },
  });
  if (!devices.length) return null;
  return (
    <details className="glass-panel group rounded-2xl border border-white/10" data-testid="hardware-hidden">
      <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-2 px-4 text-sm text-slate-300 [&::-webkit-details-marker]:hidden">
        <span>Hidden <span className="text-slate-500">({devices.length})</span></span>
        <ChevronRight size={16} className="text-slate-500 motion-safe:transition-transform group-open:rotate-90" aria-hidden />
      </summary>
      <p className="border-t border-white/5 px-4 pt-3 text-xs text-slate-500">
        Hidden devices keep working; rooms, Jarvis and voice leave them out.
      </p>
      <ul className="divide-y divide-white/5">
        {devices.map((d) => (
          <li key={d.id} className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-2"
            data-testid="hidden-device">
            <span className="min-w-0 flex-1 basis-40">
              <span className="block truncate text-sm text-slate-200" title={d.name}>{d.name}</span>
              <span className="block truncate text-xs text-slate-500">{[d.label, d.area].filter(Boolean).join(' · ')}</span>
            </span>
            <button type="button" disabled={show.isPending} onClick={() => show.mutate(d.id)}
              aria-label={`Show ${d.name}${d.label ? ` (${d.label})` : ''}`}
              className="inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5 disabled:opacity-50">
              <Eye size={14} aria-hidden /> Show
            </button>
          </li>
        ))}
      </ul>
      {show.isError && <p className="px-4 pb-3 text-xs text-amber-300/90" role="alert">{failureText(show.error)}</p>}
    </details>
  );
}

/**
 * Devices > Your devices (admins): every physical device as a tile, by room (or by type or
 * integration), however many integrations see it (a TV that Roku, Cast and Music Assistant all
 * register is one TV). Search, type chips and "Needs attention" in one row; add-ons and
 * assistants, which are not devices in the house, folded away at the end.
 */
export default function HardwarePanel({ onAdd }: { onAdd?: () => void }) {
  const [query, setQuery] = useState('');
  const [group, setGroup] = useState<GroupMode>('area');
  const [type, setType] = useState('');
  const [attention, setAttention] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({ queryKey: HARDWARE_KEY, queryFn: getHardware, retry: false, refetchInterval: 30_000 });
  const states = useQuery({ queryKey: STATES_KEY, queryFn: getRooms, retry: false, refetchInterval: 15_000,
    refetchIntervalInBackground: false });

  const raw = useMemo(() => list.data?.devices ?? [], [list.data]);
  const { devices, services } = useMemo(() => physicalDevices(raw), [raw]);
  const hidden = useMemo(() => hiddenDevices(raw), [raw]);
  const byDevice = useMemo(() => statesByDevice(states.data), [states.data]);
  const rows: Row[] = useMemo(() => devices.map((device) => {
    const entities = device.views.flatMap((v) => byDevice.get(v.id) ?? []);
    const kind = tileKind(device, primaryEntity(device.kind, entities));
    return { device, entities, kind, type: typeFilterOf(kind) };
  }), [devices, byDevice]);
  const attn = useMemo(() => needsAttention(devices), [devices]);
  const attnIds = useMemo(() => new Set([...attn.offline, ...attn.noRoom].map((d) => d.id)), [attn]);
  const typeCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of rows) m.set(r.type, (m.get(r.type) ?? 0) + 1);
    return TYPE_FILTERS.filter((f) => m.has(f.id)).map((f) => ({ ...f, count: m.get(f.id)! }));
  }, [rows]);

  const shown = useMemo(() => {
    const matching = new Set(filterDevices(devices, { query }).map((d) => d.id));
    return rows.filter((r) => matching.has(r.device.id) && (!type || r.type === type)
      && (!attention || attnIds.has(r.device.id)));
  }, [rows, devices, query, type, attention, attnIds]);

  const groups = useMemo(() => {
    const out = new Map<string, { key: string; label: string; rows: Row[] }>();
    for (const r of shown) {
      const [key, label] = group === 'area' ? [r.device.area_id || '', r.device.area || 'No room']
        : group === 'type' ? [r.type, TYPE_FILTERS.find((f) => f.id === r.type)?.label ?? 'Other']
          : [r.device.domain || '', r.device.label || 'Other'];
      if (!out.has(key)) out.set(key, { key, label, rows: [] });
      out.get(key)!.rows.push(r);
    }
    const order = TYPE_FILTERS.map((f) => f.id);
    return [...out.values()].sort((a, b) => {
      if (group === 'type') return order.indexOf(a.key) - order.indexOf(b.key);
      if (!a.key) return 1;
      if (!b.key) return -1;
      return a.label.localeCompare(b.label);
    });
  }, [shown, group]);

  const areas = list.data?.areas ?? [];
  const openRow = rows.find((r) => r.device.id === open);
  const openService = services.find((s) => s.id === open);
  const filtered = Boolean(query || type || attention);

  return (
    <div className="space-y-4" data-testid="hardware">
      <div className="glass-panel space-y-3 rounded-2xl border border-white/10 p-3 sm:p-4">
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative min-w-0 flex-1 sm:basis-60">
            <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search devices"
              aria-label="Search devices"
              className="min-h-11 w-full rounded-xl border border-white/10 bg-black/30 pl-9 pr-3 text-sm text-white placeholder:text-slate-500" />
          </label>
          <div role="radiogroup" aria-label="Group devices by" className="order-last flex w-full min-w-0 gap-1 rounded-xl border border-white/10 bg-black/20 p-1 md:order-none md:w-auto">
            {([['area', 'Rooms'], ['type', 'Types'], ['integration', 'Integrations']] as const).map(([id, label]) => (
              <button key={id} type="button" role="radio" aria-checked={group === id} onClick={() => setGroup(id)}
                className={`${SEG} ${group === id ? 'bg-white/10 font-semibold text-white' : 'text-slate-400 hover:text-slate-200'}`}>
                {label}
              </button>
            ))}
          </div>
          {onAdd && (
            <button type="button" onClick={onAdd} data-testid="add-device"
              aria-label="Add device"
              className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-sky-500 px-3 text-sm font-semibold text-slate-950 hover:bg-sky-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white sm:px-4">
              <Plus size={18} aria-hidden /> <span className="max-sm:sr-only">Add device</span>
            </button>
          )}
        </div>
        {list.data && devices.length > 0 && (
          <div className="-mx-3 flex gap-2 overflow-x-auto px-3 pb-0.5 touch-pan-x sm:-mx-4 sm:px-4" role="group"
            aria-label="Filter devices" data-testid="hardware-filters">
            <button type="button" className={!type && !attention ? CHIP_ON : CHIP_OFF} aria-pressed={!type && !attention}
              onClick={() => { setType(''); setAttention(false); }}>All <span className="text-slate-500">{devices.length}</span></button>
            {attnIds.size > 0 && (
              <button type="button" aria-pressed={attention} onClick={() => setAttention(!attention)}
                className={attention ? `${CHIP} border-amber-400/60 bg-amber-500/15 text-amber-100` : `${CHIP_OFF} text-amber-200`}>
                <AlertTriangle size={14} className="text-amber-300" aria-hidden /> Needs attention ({attnIds.size})
              </button>
            )}
            {typeCounts.length > 1 && typeCounts.map((t) => (
              <button key={t.id} type="button" className={type === t.id ? CHIP_ON : CHIP_OFF} aria-pressed={type === t.id}
                onClick={() => setType(type === t.id ? '' : t.id)}>
                {t.label} <span className="text-slate-500">{t.count}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {attention && <NoRoom devices={attn.noRoom} areas={areas} />}

      {list.isLoading && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5" role="status" aria-label="Loading devices">
          {[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="skeleton h-[7.25rem] rounded-2xl" />)}
        </div>
      )}
      {list.error && <p className="glass-panel rounded-2xl border border-white/10 p-4 text-sm text-amber-300/90" role="alert"
        data-testid="hardware-error">{failureText(list.error)}</p>}
      {list.data && shown.length === 0 && (
        <div className="glass-panel rounded-2xl border border-white/10 p-6 text-center" data-testid="hardware-empty">
          <p className="text-sm text-slate-300">
            {devices.length === 0 ? 'No devices yet.' : attention ? 'Nothing needs attention.' : 'No device matches.'}
          </p>
          {devices.length === 0 && onAdd && (
            <button type="button" onClick={onAdd} className="mt-3 inline-flex min-h-11 items-center gap-1.5 rounded-xl bg-sky-500 px-4 text-sm font-semibold text-slate-950">
              <Plus size={18} aria-hidden /> Add your first device
            </button>
          )}
          {devices.length > 0 && filtered && (
            <button type="button" onClick={() => { setQuery(''); setType(''); setAttention(false); }}
              className="mt-3 inline-flex min-h-11 items-center gap-1.5 rounded-xl border border-white/10 px-4 text-sm text-slate-200 hover:bg-white/5">
              <X size={16} aria-hidden /> Clear filters
            </button>
          )}
        </div>
      )}

      {groups.map((g) => (
        <section key={g.key || 'none'} data-testid="hardware-group" aria-label={g.label}>
          <h3 className="mb-2 flex items-baseline gap-2 px-1 text-base font-semibold text-white">
            {g.label} <span className="text-sm font-normal text-slate-500">{g.rows.length}</span>
          </h3>
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
            {g.rows.map((r) => <DeviceTile key={r.device.id} device={r.device} entities={r.entities} onOpen={() => setOpen(r.device.id)} />)}
          </ul>
        </section>
      ))}

      {services.length > 0 && !filtered && (
        <details className="glass-panel group rounded-2xl border border-white/10" data-testid="hardware-services">
          <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-2 px-4 text-sm text-slate-300 [&::-webkit-details-marker]:hidden">
            <span>Not devices: add-ons, assistants and accounts <span className="text-slate-500">({services.length})</span></span>
            <ChevronRight size={16} className="text-slate-500 motion-safe:transition-transform group-open:rotate-90" aria-hidden />
          </summary>
          <ul className="divide-y divide-white/5 border-t border-white/5">
            {services.map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => setOpen(d.id)} data-testid="hardware-service"
                  className="flex min-h-12 w-full items-center justify-between gap-3 px-4 text-left hover:bg-white/5">
                  <span className="min-w-0 truncate text-sm text-slate-200">{d.name}</span>
                  <span className="shrink-0 text-xs text-slate-500">{d.label || STATUS_LABEL[d.status]}</span>
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      {!filtered && <HiddenDevices devices={hidden} />}

      {open && (
        <HubDeviceSheet deviceId={open} summary={(openRow?.device ?? openService ?? null) as HardwareDevice | null}
          areas={areas} onClose={() => setOpen(null)} />
      )}
    </div>
  );
}
