import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Cpu, Search } from 'lucide-react';
import HubDeviceSheet from './HubDeviceSheet';
import { failureText } from '../../lib/discoveredDevices';
import {
  STATUS_LABEL, STATUS_TONE, abilityLabel, ago, filterDevices, getHardware, groupDevices, headlineAbilities,
  integrationsIn, type GroupBy, type HardwareDevice,
} from '../../lib/hardware';

export const HARDWARE_KEY = ['homehub-hardware'];

const CHIP = 'inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-full border px-3 text-xs';
const CHIP_OFF = `${CHIP} border-white/10 text-slate-300 hover:bg-white/5`;
const CHIP_ON = `${CHIP} border-sky-400/50 bg-sky-500/15 text-sky-100`;

function DeviceRow({ device, onOpen }: { device: HardwareDevice; onOpen: () => void }) {
  const ip = device.connections.find((c) => c.type === 'ip')?.value;
  const where = [device.area, device.label, ip].filter(Boolean).join(' · ');
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
 * Devices > Hardware (admins): every device the hub has, built from its live
 * registry and the drivers' own device info (HA's Settings > Devices). Search,
 * filter by integration or status, group by room, integration or status, and
 * open a device for its entities, their states and controls.
 */
export default function HardwarePanel() {
  const [query, setQuery] = useState('');
  const [groupBy, setGroupBy] = useState<GroupBy>('area');
  const [integration, setIntegration] = useState('');
  const [status, setStatus] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({ queryKey: HARDWARE_KEY, queryFn: getHardware, retry: false, refetchInterval: 30_000 });

  const devices = useMemo(() => list.data?.devices ?? [], [list.data]);
  const shown = useMemo(() => filterDevices(devices, { query, integration, status }), [devices, query, integration, status]);
  const groups = useMemo(() => groupDevices(shown, groupBy), [shown, groupBy]);
  const integrations = useMemo(() => integrationsIn(devices), [devices]);
  const offline = devices.filter((d) => d.status === 'offline').length;
  const partial = devices.filter((d) => d.status === 'partial').length;

  return (
    <>
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid="hardware">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 bg-emerald-500/5 px-4 py-3">
        <span className="flex min-w-0 items-center gap-2 text-[10px] font-black uppercase tracking-widest text-emerald-300">
          <Cpu size={14} /> Hardware
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
            className="min-h-11 rounded-lg border border-white/10 bg-black/30 px-2 text-xs text-white">
            <option value="area">By room</option>
            <option value="integration">By integration</option>
            <option value="status">By status</option>
            <option value="none">No groups</option>
          </select>
        </span>
      </header>

      {list.data && devices.length > 0 && (
        <div className="flex gap-2 overflow-x-auto touch-pan-x border-b border-white/5 px-4 py-2" role="group"
          aria-label="Filter devices" data-testid="hardware-filters">
          <button type="button" className={!integration && !status ? CHIP_ON : CHIP_OFF} aria-pressed={!integration && !status}
            onClick={() => { setIntegration(''); setStatus(''); }}>All</button>
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
          {integrations.map((i) => (
            <button key={i.domain} type="button" className={integration === i.domain ? CHIP_ON : CHIP_OFF}
              aria-pressed={integration === i.domain}
              onClick={() => setIntegration(integration === i.domain ? '' : i.domain)}>
              {i.label} <span className="text-slate-500">{i.count}</span>
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
          {devices.length === 0 ? 'The hub has no devices yet. Add one under New devices.' : 'No device matches.'}
        </p>
      )}

      {groups.map((g) => g.devices.length > 0 && (
        <div key={g.key} data-testid="hardware-group">
          {groupBy !== 'none' && (
            <h3 className="sticky top-0 z-[1] border-y border-white/5 bg-slate-900/80 px-4 py-1.5 text-[10px] font-black uppercase tracking-widest text-slate-400 backdrop-blur">
              {g.label} <span className="font-semibold text-slate-500">{g.devices.length}</span>
            </h3>
          )}
          <ul className="divide-y divide-white/5">
            {g.devices.map((d) => <DeviceRow key={d.id} device={d} onOpen={() => setOpen(d.id)} />)}
          </ul>
        </div>
      ))}

    </section>
    {/* Outside the panel: its backdrop blur would otherwise be the fixed sheet's containing block. */}
    {open && <HubDeviceSheet deviceId={open} areas={list.data?.areas ?? []} onClose={() => setOpen(null)} />}
    </>
  );
}
