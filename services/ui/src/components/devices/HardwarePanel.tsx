import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ChevronRight, Cpu, Search } from 'lucide-react';
import HubDeviceSheet from './HubDeviceSheet';
import { failureText, updateHubDevice } from '../../lib/discoveredDevices';
import {
  STATUS_LABEL, STATUS_TONE, abilityLabel, ago, filterDevices, getHardware, groupDevices, headlineAbilities,
  integrationsIn, kindSections, kindsIn, needsAttention, physicalDevices,
  type GroupBy, type HardwareDevice, type PhysicalDevice,
} from '../../lib/hardware';

export const HARDWARE_KEY = ['homehub-hardware'];

const CHIP = 'inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-full border px-3 text-xs';
const CHIP_OFF = `${CHIP} border-white/10 text-slate-300 hover:bg-white/5`;
const CHIP_ON = `${CHIP} border-sky-400/50 bg-sky-500/15 text-sky-100`;
const SELECT = 'min-h-11 min-w-0 rounded-lg border border-white/10 bg-black/30 px-2 text-xs text-white';
const ROOM_HEAD = 'sticky top-0 z-[1] border-y border-white/5 bg-slate-900/80 px-4 py-1.5 text-[10px] font-black uppercase tracking-widest text-slate-400 backdrop-blur';
const KIND_HEAD = 'px-4 pt-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500';

/** "Roku · Music Assistant" for a device several integrations see; its own integration otherwise. */
function seenBy(device: PhysicalDevice): string {
  const labels = [...new Set(device.views.map((v) => v.label).filter(Boolean))];
  return labels.join(' · ');
}

function DeviceRow({ device, onOpen }: { device: PhysicalDevice | HardwareDevice; onOpen: () => void }) {
  const ip = device.connections.find((c) => c.type === 'ip')?.value;
  const via = 'views' in device ? seenBy(device) : device.label;
  const where = [device.area, via, ip].filter(Boolean).join(' · ');
  const make = [device.manufacturer, device.model].filter(Boolean).join(' ');
  const chips = headlineAbilities(device.abilities, 3);
  return (
    <li>
      <button type="button" onClick={onOpen} data-testid="hardware-device"
        className="flex min-h-14 w-full min-w-0 items-center gap-3 px-4 py-2.5 text-left hover:bg-white/5"
        aria-label={`${device.name}, ${STATUS_LABEL[device.status] || device.status}`}>
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${STATUS_TONE[device.status] || 'bg-slate-500'}`} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold text-white">{device.name}</span>
          <span className="block truncate text-xs text-slate-400">{make || where}</span>
          {make && where && <span className="block truncate text-[11px] text-slate-500">{where}</span>}
          {chips.length > 0 && (
            <span className="mt-1 flex flex-wrap gap-1" aria-label="Can">
              {chips.map((a) => (
                <span key={a} className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-slate-300">{abilityLabel(a)}</span>
              ))}
            </span>
          )}
        </span>
        <span className="hidden shrink-0 text-right text-[11px] text-slate-500 sm:block">
          {device.status === 'online' || device.status === 'partial'
            ? (device.last_seen ? ago(device.last_seen) : '')
            : STATUS_LABEL[device.status]}
          {device.unavailable > 0 && device.status !== 'disabled' && (
            <span className="block text-amber-300/80">{device.unavailable} of {device.entities} unavailable</span>
          )}
        </span>
        <ChevronRight size={16} className="shrink-0 text-slate-500" aria-hidden />
      </button>
    </li>
  );
}

/**
 * What needs a person, above the list: devices in no room (each with a room picker, so a
 * whole house can be sorted in a few minutes) and devices that stopped answering.
 */
function NeedsAttention({ noRoom, offline, areas, onShowOffline }: {
  noRoom: PhysicalDevice[]; offline: number; areas: { area_id: string; name: string }[]; onShowOffline: () => void;
}) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const move = useMutation({
    // Every integration's view of the device moves together, so they never disagree on the room.
    mutationFn: async ({ device, area }: { device: PhysicalDevice; area: string }) => {
      for (const v of device.views) await updateHubDevice(v.id, { area_id: area });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: HARDWARE_KEY }),
  });
  if (noRoom.length === 0 && offline === 0) return null;
  return (
    <div className="border-b border-white/5 bg-amber-500/5 px-4 py-2" data-testid="needs-attention">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest text-amber-300">
          <AlertTriangle size={12} aria-hidden /> Needs attention
        </span>
        {noRoom.length > 0 && (
          <button type="button" className={open ? CHIP_ON : CHIP_OFF} aria-expanded={open}
            onClick={() => setOpen(!open)}>No room {noRoom.length}</button>
        )}
        {offline > 0 && (
          <button type="button" className={CHIP_OFF} onClick={onShowOffline}>Not answering {offline}</button>
        )}
      </div>
      {open && (
        <ul className="mt-2 divide-y divide-white/5" data-testid="no-room-list">
          {noRoom.map((d) => (
            <li key={d.id} className="flex min-w-0 flex-wrap items-center justify-between gap-2 py-1.5">
              <span className="min-w-0 flex-1 truncate text-sm text-slate-200" title={d.name}>{d.name}</span>
              <select className={SELECT} defaultValue="" aria-label={`Room for ${d.name}`}
                disabled={move.isPending} onChange={(e) => e.target.value && move.mutate({ device: d, area: e.target.value })}>
                <option value="" disabled>Choose a room</option>
                {areas.map((a) => <option key={a.area_id} value={a.area_id}>{a.name}</option>)}
              </select>
            </li>
          ))}
        </ul>
      )}
      {move.isError && <p className="mt-1 text-xs text-amber-300/90">{failureText(move.error)}</p>}
    </div>
  );
}

/**
 * Devices > Your devices (admins): one row per physical device, however many integrations see
 * it (a TV that Roku, Cast and Music Assistant all register is one TV), by room and then by
 * kind. Needs attention first; search, type chips, an integration menu and other groupings;
 * add-ons and assistants, which are not devices in the house, folded away at the end.
 */
export default function HardwarePanel() {
  const [query, setQuery] = useState('');
  const [groupBy, setGroupBy] = useState<GroupBy>('area');
  const [integration, setIntegration] = useState('');
  const [status, setStatus] = useState('');
  const [kind, setKind] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({ queryKey: HARDWARE_KEY, queryFn: getHardware, retry: false, refetchInterval: 30_000 });

  const raw = useMemo(() => list.data?.devices ?? [], [list.data]);
  const { devices, services } = useMemo(() => physicalDevices(raw), [raw]);
  const shown = useMemo(() => filterDevices(devices, { query, integration, status, kind }),
    [devices, query, integration, status, kind]);
  const groups = useMemo(() => groupDevices(shown, groupBy), [shown, groupBy]);
  const integrations = useMemo(() => integrationsIn(raw.filter((d) => !d.service)), [raw]);
  const kinds = useMemo(() => kindsIn(devices), [devices]);
  const attention = useMemo(() => needsAttention(devices), [devices]);
  const offline = devices.filter((d) => d.status === 'offline').length;
  const partial = devices.filter((d) => d.status === 'partial').length;
  const areas = list.data?.areas ?? [];
  const clear = () => { setIntegration(''); setStatus(''); setKind(''); };

  return (
    <>
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid="hardware">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 bg-emerald-500/5 px-4 py-3">
        <span className="flex min-w-0 items-center gap-2 text-[10px] font-black uppercase tracking-widest text-emerald-300">
          <Cpu size={14} /> Your devices
          {list.data && <span className="font-semibold normal-case tracking-normal text-slate-400">{devices.length} devices</span>}
        </span>
        <span className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto">
          <label className="relative min-w-0 flex-1 sm:w-56 sm:flex-none">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search devices"
              aria-label="Search devices"
              className="min-h-11 w-full rounded-lg border border-white/10 bg-black/30 pl-8 pr-3 text-xs text-white" />
          </label>
          <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupBy)} aria-label="Group devices by"
            className={SELECT}>
            <option value="area">By room</option>
            <option value="kind">By type</option>
            <option value="integration">By integration</option>
            <option value="status">By status</option>
            <option value="none">No groups</option>
          </select>
          {integrations.length > 1 && (
            <select value={integration} onChange={(e) => setIntegration(e.target.value)} aria-label="Filter by integration"
              className={SELECT}>
              <option value="">All integrations</option>
              {integrations.map((i) => <option key={i.domain} value={i.domain}>{i.label} ({i.count})</option>)}
            </select>
          )}
        </span>
      </header>

      {list.data && (
        <NeedsAttention noRoom={attention.noRoom} offline={attention.offline.length} areas={areas}
          onShowOffline={() => { clear(); setStatus('offline'); }} />
      )}

      {list.data && devices.length > 0 && (
        <div className="flex gap-2 overflow-x-auto touch-pan-x border-b border-white/5 px-4 py-2" role="group"
          aria-label="Filter devices" data-testid="hardware-filters">
          <button type="button" className={!integration && !status && !kind ? CHIP_ON : CHIP_OFF}
            aria-pressed={!integration && !status && !kind} onClick={clear}>All</button>
          {offline > 0 && (
            <button type="button" className={status === 'offline' ? CHIP_ON : CHIP_OFF} aria-pressed={status === 'offline'}
              onClick={() => setStatus(status === 'offline' ? '' : 'offline')}>
              <span className="h-2 w-2 rounded-full bg-rose-400" aria-hidden /> Offline {offline}
            </button>
          )}
          {partial > 0 && (
            <button type="button" className={status === 'partial' ? CHIP_ON : CHIP_OFF} aria-pressed={status === 'partial'}
              onClick={() => setStatus(status === 'partial' ? '' : 'partial')}>
              <span className="h-2 w-2 rounded-full bg-amber-400" aria-hidden /> Partly offline {partial}
            </button>
          )}
          {kinds.length > 1 && kinds.map((k) => (
            <button key={k.kind} type="button" className={kind === k.kind ? CHIP_ON : CHIP_OFF}
              aria-pressed={kind === k.kind} onClick={() => setKind(kind === k.kind ? '' : k.kind)}>
              {k.label} <span className="text-slate-500">{k.count}</span>
            </button>
          ))}
        </div>
      )}

      {list.isLoading && <div className="space-y-2 p-4" role="status" aria-label="Loading devices">
        {[0, 1, 2].map((i) => <div key={i} className="skeleton h-14 w-full rounded-xl" />)}
      </div>}
      {list.error && <p className="p-4 text-sm text-amber-300/90" data-testid="hardware-error">{failureText(list.error)}</p>}
      {list.data && shown.length === 0 && (
        <p className="p-4 text-sm text-slate-500" data-testid="hardware-empty">
          {devices.length === 0 ? 'The hub has no devices yet. Add one under Add & fix.' : 'No device matches.'}
        </p>
      )}

      {groups.map((g) => g.devices.length > 0 && (
        <div key={g.key} data-testid="hardware-group">
          {groupBy !== 'none' && (
            <h3 className={ROOM_HEAD}>
              {g.label} <span className="font-semibold text-slate-500">{g.devices.length}</span>
            </h3>
          )}
          {groupBy === 'area' && !kind ? (
            kindSections(g.devices).map((s) => (
              <div key={s.kind} data-testid="hardware-kind">
                <h4 className={KIND_HEAD}>{s.label}</h4>
                <ul className="divide-y divide-white/5">
                  {s.devices.map((d) => <DeviceRow key={d.id} device={d} onOpen={() => setOpen(d.id)} />)}
                </ul>
              </div>
            ))
          ) : (
            <ul className="divide-y divide-white/5">
              {g.devices.map((d) => <DeviceRow key={d.id} device={d} onOpen={() => setOpen(d.id)} />)}
            </ul>
          )}
        </div>
      ))}

      {services.length > 0 && (
        <details className="border-t border-white/5" data-testid="hardware-services">
          <summary className="flex min-h-11 cursor-pointer items-center px-4 text-xs text-slate-400">
            Not devices: add-ons, assistants and accounts ({services.length})
          </summary>
          <ul className="divide-y divide-white/5">
            {services.map((d) => <DeviceRow key={d.id} device={d} onOpen={() => setOpen(d.id)} />)}
          </ul>
        </details>
      )}
    </section>
    {/* Outside the panel: its backdrop blur would otherwise be the fixed sheet's containing block. */}
    {open && <HubDeviceSheet deviceId={open} areas={areas} onClose={() => setOpen(null)} />}
    </>
  );
}
