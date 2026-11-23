import { useState, type FC, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Cable, Camera, Gamepad2, Laptop, Lightbulb, Monitor, Package, Printer, Router, Search,
  Smartphone, Speaker, Tablet, Tv, Watch,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { relativeTime } from '../../lib/wanderTrips';
import {
  NETWORK_DEVICE_KINDS,
  type NetworkDevice,
  type NetworkDeviceFields,
  type NetworkDeviceKind,
} from '../../lib/network';
import type { UserProfile } from '../../types/api';
import { confirmDialog } from '../ui/confirmDialog';
import Modal from '../ui/Modal';

/**
 * The device registry in Admin > Network (docs/NETWORK.md, "Devices").
 *
 * Detection fills in what it can from the browser, DNS, the OUI table and the
 * hostname; an admin's hand-set details win. The list shows the best name we
 * have and the editor shows where each detected answer came from, so it is
 * clear what a person is changing.
 */

const DETAIL_KEYS: (keyof NetworkDeviceFields)[] = [
  'alias', 'kind', 'make', 'model', 'os', 'owner', 'notes',
];

const KIND_LABELS: Record<NetworkDeviceKind, string> = {
  phone: 'Phone', tablet: 'Tablet', laptop: 'Laptop', desktop: 'Desktop', tv: 'TV',
  streaming: 'Streaming box', speaker: 'Speaker', smart_home: 'Smart home', camera: 'Camera',
  printer: 'Printer', console: 'Console', network: 'Network gear', wearable: 'Wearable',
  other: 'Other', unknown: 'Unknown',
};

const KIND_ICONS: Record<NetworkDeviceKind, React.ElementType> = {
  phone: Smartphone, tablet: Tablet, laptop: Laptop, desktop: Monitor, tv: Tv,
  streaming: Router, speaker: Speaker, smart_home: Lightbulb, camera: Camera,
  printer: Printer, console: Gamepad2, network: Router, wearable: Watch,
  other: Package, unknown: Package,
};

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'new', label: 'New' },
  { id: 'unknown', label: 'Unknown kind' },
] as const;
type Filter = (typeof FILTERS)[number]['id'];

function deviceName(d: NetworkDevice): string {
  if (d.alias) return d.alias;
  const makeModel = [d.make, d.model].filter(Boolean).join(' ');
  if (makeModel) return makeModel;
  if (d.kind) return d.kind === 'unknown' ? KIND_LABELS.unknown : `${KIND_LABELS[d.kind]}${d.make ? ` (${d.make})` : ''}`;
  return d.mac;
}

/** First seen this week and nobody has added a hand-set detail yet. */
function isNewDevice(d: NetworkDevice, nowSeconds: number = Date.now() / 1000): boolean {
  if (!d.first_seen || nowSeconds - d.first_seen > 7 * 86400) return false;
  const manualDetails = [d.alias, d.owner, d.notes].some(Boolean)
    || [d.kind_source, d.make_source, d.model_source, d.os_source].includes('manual');
  return !manualDetails;
}

function sourceLabel(source: string | null | undefined): string {
  switch (source) {
    case 'user_agent':
    case 'ua':
      return 'detected from its browser';
    case 'dns':
      return 'from DNS';
    case 'oui':
      return "from the maker's address";
    case 'hostname':
      return 'from its name';
    case 'mdns':
      return 'announced by the device';
    case 'dhcp':
      return 'from how it asked for an address';
    case 'manual':
      return 'set by you';
    default:
      return '';
  }
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function draftFor(d: NetworkDevice): NetworkDeviceFields {
  return {
    alias: d.alias ?? '', kind: d.kind ?? '', make: d.make ?? '', model: d.model ?? '',
    os: d.os ?? '', owner: d.owner ?? '', notes: d.notes ?? '',
  };
}

/** Only the fields whose value actually changed; '' means "return this to detection". */
function changedFields(draft: NetworkDeviceFields, original: NetworkDeviceFields): NetworkDeviceFields {
  const changes: NetworkDeviceFields = {};
  for (const key of DETAIL_KEYS) {
    const next = text(draft[key]);
    if (next !== text(original[key])) (changes as Record<string, string>)[key] = next;
  }
  return changes;
}

function personName(user: UserProfile): string {
  return user.display_name || user.full_name || user.username;
}

const NetworkDevices: FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const [picked, setPicked] = useState<NetworkDevice | null>(null);

  const { data, isPending, isError } = useQuery({
    queryKey: ['network-devices'],
    queryFn: () => api.getNetworkDevices(),
  });
  const devices = data?.devices ?? [];

  // Admin > New device notice links to /admin/network?device=<mac>. The Admin
  // page picks the network section from the path; this opens the device. A tap
  // on another row wins until this editor is closed.
  const linkedMac = (searchParams.get('device') || '').toLowerCase();
  const linkedDevice = linkedMac
    ? devices.find((d) => d.mac.toLowerCase() === linkedMac) ?? null
    : null;
  const editing = picked ?? linkedDevice;
  const closeEditor = () => {
    setPicked(null);
    if (!linkedMac) return;
    const next = new URLSearchParams(searchParams);
    next.delete('device');
    setSearchParams(next, { replace: true });
  };

  const { data: users = [] } = useQuery<UserProfile[]>({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
    enabled: !!editing,
  });

  const visible = devices.filter((d) => {
    if (filter === 'new' && !isNewDevice(d)) return false;
    if (filter === 'unknown' && d.kind && d.kind !== 'unknown') return false;
    const needle = search.trim().toLowerCase();
    if (!needle) return true;
    return [deviceName(d), d.mac, d.kind, d.make, d.model, d.os, d.owner]
      .some((part) => text(part).toLowerCase().includes(needle));
  });

  return (
    <section className="glass-panel p-4 sm:p-5" data-testid="network-devices">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
            <Cable size={15} className="text-purple-300" /> Devices
          </h3>
          <p className="mt-0.5 text-[11px] text-slate-500">
            Every device Jarvis has seen on the home network, with its details.
          </p>
        </div>
        <span className="text-[11px] text-slate-400">{devices.length} known</span>
      </div>

      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
        <label className="relative block w-full sm:max-w-xs">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search devices"
            placeholder="Search devices"
            className="glass-input min-h-11 w-full pl-9 pr-3 text-sm"
          />
        </label>
        <div role="group" aria-label="Filter devices" className="flex flex-wrap gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
              className={`min-h-11 sm:min-h-9 rounded-lg px-2.5 text-xs font-semibold ${filter === f.id
                ? 'bg-purple-500/20 text-purple-300'
                : 'text-slate-400 hover:text-white'}`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {isPending ? (
        <p className="text-xs text-slate-500">Loading devices…</p>
      ) : isError ? (
        <p className="text-xs text-rose-300">Could not load the device registry.</p>
      ) : visible.length === 0 ? (
        <p className="text-xs text-slate-500">{devices.length ? 'No devices match that search.' : 'No devices yet.'}</p>
      ) : (
        <ul className="divide-y divide-white/5">
          {visible.map((d) => {
            const Icon = KIND_ICONS[d.kind ?? 'unknown'] ?? Package;
            return (
              <li key={d.mac}>
                <button
                  type="button"
                  onClick={() => setPicked(d)}
                  aria-label={`Edit ${deviceName(d)}`}
                  className="flex min-h-11 w-full items-center gap-3 py-2.5 text-left hover:bg-white/5"
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/5">
                    <Icon size={16} className="text-purple-300" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="truncate text-sm font-medium text-white">{deviceName(d)}</span>
                      {isNewDevice(d) && (
                        <span className="shrink-0 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-emerald-300">
                          New
                        </span>
                      )}
                      {d.private_mac && (
                        <span className="shrink-0 rounded-full border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] font-semibold text-slate-300">
                          Private address
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block truncate text-[11px] text-slate-400">
                      {[d.kind ? KIND_LABELS[d.kind] : '', d.make, d.model, d.os].filter(Boolean).join(' · ') || 'No details yet'}
                    </span>
                    <span className="mt-0.5 block truncate text-[10px] text-slate-500">
                      {[
                        d.owner ? `Owner: ${d.owner}` : '',
                        d.ip || '',
                        d.last_seen ? `last seen ${relativeTime(d.last_seen)}` : 'never seen',
                      ].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <span
                      role="img"
                      aria-label={d.online ? 'Online' : 'Offline'}
                      title={d.online ? 'Online' : 'Offline'}
                      className={`h-2.5 w-2.5 rounded-full ${d.online ? 'bg-emerald-400' : 'bg-slate-600'}`}
                    />
                    <span className="hidden font-mono text-[10px] text-slate-500 lg:inline">{d.mac}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {editing && (
        <DeviceEditor
          key={editing.mac}
          device={editing}
          users={users}
          onClose={closeEditor}
        />
      )}
    </section>
  );
};

const DeviceEditor: FC<{ device: NetworkDevice; users: UserProfile[]; onClose: () => void }> = ({ device, users, onClose }) => {
  const queryClient = useQueryClient();
  const original = draftFor(device);
  const [draft, setDraft] = useState<NetworkDeviceFields>(original);
  const set = (key: keyof NetworkDeviceFields, value: string) =>
    setDraft((prev) => ({ ...prev, [key]: value }));

  const save = useMutation({
    mutationFn: (fields: NetworkDeviceFields) => api.updateNetworkDevice(device.mac, fields),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['network-devices'] });
      toast.success('Device details saved');
      onClose();
    },
    onError: () => toast.error('Could not save the device details'),
  });
  const forget = useMutation({
    mutationFn: () => api.forgetNetworkDevice(device.mac),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['network-devices'] });
      toast.success('Device forgotten');
      onClose();
    },
    onError: () => toast.error('Could not forget the device'),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const changes = changedFields(draft, original);
    if (Object.keys(changes).length === 0) {
      onClose();
      return;
    }
    save.mutate(changes);
  };

  const askForget = async () => {
    const yes = await confirmDialog({
      title: 'Forget this device?',
      body: `Jarvis will drop its record of ${deviceName(device)}. Detection can add it back the next time it is seen.`,
      confirmLabel: 'Forget device',
      danger: true,
    });
    if (yes) forget.mutate();
  };

  const field = (
    key: keyof NetworkDeviceFields,
    label: string,
    control: React.ReactNode,
    hint?: string,
  ) => {
    const hasValue = !!text(draft[key]);
    return (
      <div className="space-y-1">
        <div className="flex min-h-11 flex-wrap items-center justify-between gap-2">
          <label htmlFor={`network-device-${key}`} className="text-xs font-semibold text-slate-300">
            {label}
            {hint && <span className="ml-1 font-normal text-slate-500">({hint})</span>}
          </label>
          {hasValue && (
            <button
              type="button"
              onClick={() => set(key, '')}
              aria-label={`Clear ${label}`}
              className="glass-button min-h-11 px-3 text-xs font-semibold"
            >
              Clear
            </button>
          )}
        </div>
        {control}
      </div>
    );
  };

  const inputClass = 'glass-input min-h-11 w-full px-3 text-sm';
  const source = (key: 'kind' | 'make' | 'model' | 'os') => {
    const value = sourceLabel(device[`${key}_source`]);
    return value || undefined;
  };
  const ownerMissing = !!draft.owner && !users.some((u) => u.username === draft.owner);

  return (
    <Modal isOpen onClose={onClose} title="Edit device" size="xl">
      <form onSubmit={submit} className="space-y-3">
        <p className="break-all text-[11px] text-slate-500">{device.mac}</p>
        {field('alias', 'Alias', (
          <input
            id="network-device-alias"
            className={inputClass}
            value={text(draft.alias)}
            onChange={(e) => set('alias', e.target.value)}
            placeholder="What the family calls it"
          />
        ))}
        {field('kind', 'Kind', (
          <select
            id="network-device-kind"
            className={inputClass}
            value={text(draft.kind)}
            onChange={(e) => set('kind', e.target.value as NetworkDeviceKind | '')}
          >
            <option value="">Unknown</option>
            {NETWORK_DEVICE_KINDS.filter((k) => k !== 'unknown').map((k) => (
              <option key={k} value={k}>{KIND_LABELS[k]}</option>
            ))}
          </select>
        ), source('kind'))}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {field('make', 'Make', (
            <input id="network-device-make" className={inputClass} value={text(draft.make)}
              onChange={(e) => set('make', e.target.value)} />
          ), source('make'))}
          {field('model', 'Model', (
            <input id="network-device-model" className={inputClass} value={text(draft.model)}
              onChange={(e) => set('model', e.target.value)} />
          ), source('model'))}
        </div>
        {field('os', 'OS', (
          <input id="network-device-os" className={inputClass} value={text(draft.os)}
            onChange={(e) => set('os', e.target.value)} />
        ), source('os'))}
        {field('owner', 'Owner', (
          <select id="network-device-owner" className={inputClass} value={text(draft.owner)}
            onChange={(e) => set('owner', e.target.value)}>
            <option value="">Nobody yet</option>
            {ownerMissing && <option value={draft.owner ?? ''}>{draft.owner}</option>}
            {users.map((u) => (
              <option key={u.username} value={u.username}>{personName(u)}</option>
            ))}
          </select>
        ))}
        {field('notes', 'Notes', (
          <textarea id="network-device-notes" className={`${inputClass} min-h-20 py-2`}
            value={text(draft.notes)} onChange={(e) => set('notes', e.target.value)}
            placeholder="Anything that helps tell it apart" />
        ))}
        <div className="flex flex-col gap-2 border-t border-white/5 pt-3 sm:flex-row sm:items-center sm:justify-between">
          <button
            type="button"
            onClick={askForget}
            disabled={forget.isPending}
            className="min-h-11 rounded-xl border border-rose-500/40 px-4 text-xs font-semibold text-rose-300 hover:bg-rose-500/10"
          >
            {forget.isPending ? 'Forgetting…' : 'Forget device'}
          </button>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} className="glass-button min-h-11 px-4 text-xs font-semibold">
              Cancel
            </button>
            <button type="submit" disabled={save.isPending} className="glass-button min-h-11 px-4 text-xs font-semibold">
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
};

export default NetworkDevices;
