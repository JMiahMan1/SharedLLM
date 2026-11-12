import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { EyeOff, ExternalLink, Loader2, Pencil, Power, Trash2 } from 'lucide-react';
import Modal from '../ui/Modal';
import { EntityControl, SensorChip } from '../dashboards/EntityControls';
import { failureStatus, failureText, removeHubDevice, updateHubDevice } from '../../lib/discoveredDevices';
import {
  STATUS_LABEL, STATUS_TONE, abilityLabel, ago, getHardwareDevice, setDeviceDisabled, updateEntity,
  type HardwareDetail, type HardwareEntity,
} from '../../lib/hardware';

const BTN = 'inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5 disabled:opacity-50';
const INPUT = 'min-h-11 w-full rounded-lg border border-white/10 bg-black/30 px-3 text-sm text-white';
/** Domains whose row is a reading (a chip), not a control. */
const READINGS = new Set(['sensor', 'binary_sensor', 'weather', 'device_tracker', 'person', 'event', 'update']);

function Info({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="break-words text-sm text-slate-200">{value}</dd>
    </div>
  );
}

function EntityRow({ entity, onChanged }: { entity: HardwareEntity; onChanged: () => void }) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(entity.name_by_user || '');
  const save = useMutation({
    mutationFn: (body: Parameters<typeof updateEntity>[1]) => updateEntity(entity.entity_id, body),
    onSuccess: () => { setRenaming(false); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const disabled = Boolean(entity.disabled_by);
  const st = entity.state;
  return (
    <li className="space-y-1.5 py-2" data-testid="hardware-entity">
      {!disabled && st && !READINGS.has(entity.domain) ? (
        <EntityControl entity={st} onChanged={onChanged} />
      ) : (
        <div className="flex min-w-0 items-center justify-between gap-2">
          <span className="min-w-0 truncate text-sm text-slate-200">{entity.name}</span>
          {disabled ? <span className="shrink-0 text-xs text-slate-500">Disabled{entity.disabled_by === 'device' ? ' with the device' : ''}</span>
            : st ? <SensorChip entity={st} /> : <span className="shrink-0 text-xs text-slate-500">No state yet</span>}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
        <span className="min-w-0 break-all font-mono">{entity.entity_id}</span>
        {entity.entity_category && <span>{entity.entity_category}</span>}
        {entity.capabilities && entity.capabilities.abilities.length > 0 && (
          <span>{entity.capabilities.abilities.slice(0, 6).map(abilityLabel).join(', ')}</span>
        )}
      </div>
      {renaming ? (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ name: name.trim() || null }); }}>
          <input className={INPUT} value={name} placeholder={entity.original_name || entity.name} autoFocus
            aria-label={`New name for ${entity.name}`} onChange={(e) => setName(e.target.value)} />
          <button type="submit" className={BTN} disabled={save.isPending}>Save</button>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button type="button" className={BTN} onClick={() => setRenaming(true)} aria-label={`Rename ${entity.name}`}>
            <Pencil size={13} /> Rename
          </button>
          {entity.disabled_by !== 'device' && (
            <button type="button" className={BTN} disabled={save.isPending}
              onClick={() => save.mutate({ disabled: !disabled })}
              aria-label={`${disabled ? 'Enable' : 'Disable'} ${entity.name}`}>
              <EyeOff size={13} /> {disabled ? 'Enable' : 'Disable'}
            </button>
          )}
        </div>
      )}
    </li>
  );
}

function Body({ device, areas, onChanged, onRemoved }: {
  device: HardwareDetail; areas: { area_id: string; name: string }[]; onChanged: () => void; onRemoved: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(device.name_by_user || '');
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [unreachable, setUnreachable] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const rename = useMutation({
    mutationFn: () => updateHubDevice(device.id, { name_by_user: name.trim() || null }),
    onSuccess: () => { setRenaming(false); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const move = useMutation({
    mutationFn: (area: string) => updateHubDevice(device.id, { area_id: area || null }),
    onSuccess: onChanged,
    onError: (err) => toast.error(failureText(err)),
  });
  const disable = useMutation({
    mutationFn: () => setDeviceDisabled(device.id, !device.disabled_by),
    onSuccess: () => { toast.success(device.disabled_by ? `${device.name} is enabled.` : `${device.name} is disabled.`); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const remove = useMutation({
    mutationFn: (force: boolean) => removeHubDevice(device.id, force),
    onSuccess: (r) => { toast.success(r.warning ? `${device.name} was removed. ${r.warning}` : `${device.name} was removed.`); onRemoved(); },
    onError: (err) => { if (failureStatus(err) === 502) setUnreachable(failureText(err)); else toast.error(failureText(err)); },
  });
  const main = device.entity_list.filter((e) => !e.entity_category && !e.disabled_by);
  const extra = device.entity_list.filter((e) => e.entity_category || e.disabled_by);

  return (
    <div className="space-y-4" data-testid="hardware-detail">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${STATUS_TONE[device.status] || 'bg-slate-500'}`} aria-hidden />
        <span className="text-sm text-slate-300">{STATUS_LABEL[device.status] || device.status}</span>
        {device.last_seen && <span className="text-xs text-slate-500">· seen {ago(device.last_seen)}</span>}
      </div>
      {device.entry_reason && <p className="text-xs text-amber-300/90" role="status">{device.entry_reason}</p>}

      {renaming ? (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); rename.mutate(); }}>
          <input className={INPUT} value={name} placeholder={device.original_name || device.name} autoFocus
            aria-label="Device name" onChange={(e) => setName(e.target.value)} />
          <button type="submit" className={BTN} disabled={rename.isPending}>Save</button>
        </form>
      ) : null}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        <Info label="Manufacturer" value={device.manufacturer} />
        <Info label="Model" value={device.model} />
        <Info label="Firmware" value={device.sw_version} />
        <Info label="Hardware" value={device.hw_version} />
        <Info label="Serial" value={device.serial_number} />
        <Info label="Integration" value={device.label ? `${device.label}${device.entry_title ? ` (${device.entry_title})` : ''}` : null} />
        <Info label="Connected through" value={device.via_device_name} />
        {device.connections.map((c) => <Info key={`${c.type}-${c.value}`} label={c.type.toUpperCase()} value={<span className="font-mono text-xs">{c.value}</span>} />)}
      </dl>

      <label className="block text-xs text-slate-400">
        Room
        <select className={`mt-1 ${INPUT}`} value={device.area_id || ''} onChange={(e) => move.mutate(e.target.value)}
          aria-label={`Room of ${device.name}`}>
          <option value="">No room</option>
          {areas.map((a) => <option key={a.area_id} value={a.area_id}>{a.name}</option>)}
        </select>
      </label>

      {device.abilities.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-slate-500">It can</p>
          <div className="mt-1 flex flex-wrap gap-1" data-testid="hardware-abilities">
            {device.abilities.map((a) => <span key={a} className="rounded-full bg-white/5 px-2 py-0.5 text-[11px] text-slate-300">{abilityLabel(a)}</span>)}
          </div>
        </div>
      )}

      <section aria-label="Entities">
        <h3 className="text-[10px] font-black uppercase tracking-widest text-slate-500">
          Entities <span className="font-semibold">{device.entity_list.length}</span>
        </h3>
        {device.entity_list.length === 0 && <p className="py-2 text-xs text-slate-500">This device has no entities.</p>}
        <ul className="divide-y divide-white/5">
          {main.map((e) => <EntityRow key={e.entity_id} entity={e} onChanged={onChanged} />)}
        </ul>
        {extra.length > 0 && (
          <>
            <button type="button" className="mt-1 min-h-11 text-xs text-slate-400 underline" onClick={() => setShowHidden((v) => !v)}>
              {showHidden ? 'Hide' : 'Show'} settings, diagnostics and disabled ({extra.length})
            </button>
            {showHidden && <ul className="divide-y divide-white/5">{extra.map((e) => <EntityRow key={e.entity_id} entity={e} onChanged={onChanged} />)}</ul>}
          </>
        )}
      </section>

      {device.children.length > 0 && (
        <p className="text-xs text-slate-400">Connects {device.children.map((c) => c.name).join(', ')}.</p>
      )}

      <div className="flex flex-wrap gap-2 border-t border-white/5 pt-3">
        <button type="button" className={BTN} onClick={() => setRenaming((v) => !v)}><Pencil size={13} /> Rename</button>
        <button type="button" className={BTN} disabled={disable.isPending} onClick={() => disable.mutate()}>
          {disable.isPending ? <Loader2 size={13} className="animate-spin" /> : <Power size={13} />} {device.disabled_by ? 'Enable' : 'Disable'}
        </button>
        {device.configuration_url && /^https?:/.test(device.configuration_url) && (
          <a href={device.configuration_url} target="_blank" rel="noopener noreferrer" className={BTN}>
            <ExternalLink size={13} /> Device page
          </a>
        )}
        {confirmRemove ? (
          <button type="button" disabled={remove.isPending} onClick={() => remove.mutate(Boolean(unreachable))}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-rose-600 px-3 text-xs font-semibold text-white disabled:opacity-50">
            {remove.isPending && <Loader2 size={13} className="animate-spin" />} {unreachable ? 'Remove anyway' : 'Yes, remove it'}
          </button>
        ) : (
          <button type="button" onClick={() => setConfirmRemove(true)}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-rose-500/30 px-3 text-xs text-rose-200 hover:bg-rose-500/10">
            <Trash2 size={13} /> Remove
          </button>
        )}
      </div>
      {confirmRemove && (
        <p className="text-xs text-slate-400" role="alert">
          {unreachable || `This removes ${device.name} and its ${device.entity_list.length} entities from the hub; automations that use them stop finding them.`}
        </p>
      )}
    </div>
  );
}

/** One device from the hub's registry: what it is, how it is reached, what it can do, its entities and their controls. */
export default function HubDeviceSheet({ deviceId, areas, onClose }: {
  deviceId: string; areas: { area_id: string; name: string }[]; onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const key = ['homehub-hardware', deviceId];
  const detail = useQuery({ queryKey: key, queryFn: () => getHardwareDevice(deviceId), retry: false, refetchInterval: 15_000 });
  const changed = () => {
    void queryClient.invalidateQueries({ queryKey: ['homehub-hardware'] });
    void queryClient.invalidateQueries({ queryKey: ['homehub-entries'] });
  };
  return (
    <Modal isOpen onClose={onClose} title={detail.data?.name || 'Device'} size="2xl">
      {detail.isLoading && <div className="space-y-2" role="status" aria-label="Loading the device">
        {[0, 1, 2].map((i) => <div key={i} className="skeleton h-10 w-full rounded-xl" />)}
      </div>}
      {detail.error && <p className="text-sm text-amber-300/90" role="alert">{failureText(detail.error)}</p>}
      {detail.data && <Body device={detail.data} areas={areas} onChanged={changed} onRemoved={() => { changed(); onClose(); }} />}
    </Modal>
  );
}
