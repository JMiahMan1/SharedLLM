import { useId, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { ChevronRight, ExternalLink, EyeOff, KeyRound, Loader2, Pencil, Power, Tag, Trash2, WifiOff } from 'lucide-react';
import DeviceSheet from './DeviceSheet';
import LabelChips from './LabelChips';
import RestartsSection from './RestartsSection';
import DeviceLogsSection from './DeviceLogsSection';
import TasmotaTemplateSection from './TasmotaTemplateSection';
import WakeWordSection from './WakeWordSection';
import DeviceControl from './controls/registry';
import DeviceGlyph from './DeviceGlyph';
import { accentFor, isActiveState } from '../../lib/deviceIcons';
import { EntityControl } from '../dashboards/EntityControls';
import { confirmDialog } from '../ui/confirmDialog';
import PasswordField from '../ui/PasswordField';
import type { HubEntity } from '../../lib/dashboards';
import {
  connectionFields, entryNeedsText, failureStatus, failureText, reconfigureEntry, removeHubDevice, updateHubDevice,
} from '../../lib/discoveredDevices';
import type { HubEntry } from '../../lib/discoveredDevices';
import {
  ago, getHardwareDevice, setDeviceDisabled, updateEntity, type HardwareDetail, type HardwareDevice, type HardwareEntity,
} from '../../lib/hardware';
import { controlKind, usedSiblings } from '../../lib/deviceControls';
import {
  entitySection, isOffline, plainName, primaryEntity, readingText, stateLine, tileKind, type EntitySection,
} from '../../lib/deviceTiles';
import { LABELS_KEY, getLabels, type Label } from '../../lib/homeSetup';
import { wakeWordEntityIds } from '../../lib/wakeWord';

const BTN = 'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm text-slate-200 hover:bg-white/10 disabled:opacity-50';
const INPUT = 'min-h-11 w-full min-w-0 rounded-xl border border-white/10 bg-black/30 px-3 text-sm text-white placeholder:text-slate-500';
const CARD = 'rounded-2xl border border-white/10 bg-white/[0.03]';

/** A titled group on the detail, like the cards on Home Assistant's device page. */
function Section({ title, children, testId }: { title: string; children: ReactNode; testId?: string }) {
  return (
    <section aria-label={title} data-testid={testId} className="space-y-2">
      <h3 className="px-1 text-sm font-semibold text-slate-300">{title}</h3>
      {children}
    </section>
  );
}

/** One entity's control or reading, under its plain name ("Default fade", not "Kauf Bulb Default fade"). */
function EntityLine({ entity, deviceName }: { entity: HardwareEntity; deviceName: string }) {
  const st = entity.state;
  const name = plainName(entity.name, deviceName);
  if (!st) {
    return (
      <div className="flex min-h-11 items-center justify-between gap-3 py-1" data-testid="hardware-entity">
        <span className="min-w-0 truncate text-sm text-slate-200">{name}</span>
        <span className="shrink-0 text-xs text-slate-500">No reading yet</span>
      </div>
    );
  }
  const section = entitySection(entity);
  if (section === 'sensors' || section === 'diagnostic') {
    return (
      <div className="flex min-h-11 items-center justify-between gap-3 py-1" data-testid="hardware-entity">
        <span className="min-w-0 truncate text-sm text-slate-200">{name}</span>
        <span className="shrink-0 text-sm tabular-nums text-white">{readingText(st)}</span>
      </div>
    );
  }
  // Anything that moves or installs asks first: the library's own control, not the compact row.
  if (['update', 'siren', 'lock', 'garage'].includes(controlKind(st))) {
    return <div className="py-2" data-testid="hardware-entity"><DeviceControl entity={st} name={deviceName} /></div>;
  }
  return <div data-testid="hardware-entity"><EntityControl entity={{ ...st, name }} /></div>;
}

/** Rename, hide and label one entity: kept for admins under Device info, with the entity ids. */
function EntityAdminRow({ entity, labels, deviceName, onChanged }: {
  entity: HardwareEntity; labels: Label[]; deviceName: string; onChanged: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [labelling, setLabelling] = useState(false);
  const [name, setName] = useState(entity.name_by_user || '');
  const save = useMutation({
    mutationFn: (body: Parameters<typeof updateEntity>[1]) => updateEntity(entity.entity_id, body),
    onSuccess: () => { setRenaming(false); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const disabled = Boolean(entity.disabled_by);
  return (
    <li className="space-y-2 py-3" data-testid="entity-admin">
      <div className="min-w-0">
        <p className="truncate text-sm text-slate-200">{plainName(entity.name, deviceName)}
          {disabled && <span className="ml-2 text-xs text-slate-500">Hidden{entity.disabled_by === 'device' ? ' with the device' : ''}</span>}
        </p>
        <p className="break-all font-mono text-[11px] text-slate-500">{entity.entity_id}</p>
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
            <Pencil size={14} aria-hidden /> Rename
          </button>
          {entity.disabled_by !== 'device' && (
            <button type="button" className={BTN} disabled={save.isPending} onClick={() => save.mutate({ disabled: !disabled })}
              aria-label={`${disabled ? 'Show' : 'Hide'} ${entity.name}`}>
              <EyeOff size={14} aria-hidden /> {disabled ? 'Show' : 'Hide'}
            </button>
          )}
          {labels.length > 0 && (
            <button type="button" className={BTN} aria-expanded={labelling} onClick={() => setLabelling((v) => !v)}
              aria-label={`Labels of ${entity.name}`}>
              <Tag size={14} aria-hidden /> Labels{entity.labels?.length ? ` (${entity.labels.length})` : ''}
            </button>
          )}
        </div>
      )}
      {labelling && (
        <LabelChips labels={labels} selected={entity.labels ?? []} name={entity.name} disabled={save.isPending}
          onChange={(next) => save.mutate({ labels: next })} />
      )}
    </li>
  );
}

/** The switch's track and knob; `mixed` sits the knob in the middle. */
function SwitchTrack({ on, mixed = false }: { on: boolean; mixed?: boolean }) {
  const bg = mixed ? 'bg-sky-700' : on ? 'bg-sky-500' : 'bg-slate-600';
  const knob = mixed ? 'translate-x-[0.875rem]' : on ? 'translate-x-6' : 'translate-x-1';
  return (
    <span className={`relative h-7 w-12 shrink-0 rounded-full motion-safe:transition-colors ${bg}`} aria-hidden>
      <span className={`absolute top-1 h-5 w-5 rounded-full bg-white motion-safe:transition-transform ${knob}`} />
    </span>
  );
}

/** "May the Voice Assistant control it", one switch per entity (Home Assistant's Assist exposure). */
function VoiceRow({ entity, deviceName, exposed, busy, onToggle }: {
  entity: HardwareEntity; deviceName: string; exposed: boolean; busy: boolean; onToggle: (voice: boolean) => void;
}) {
  const name = plainName(entity.name, deviceName);
  return (
    <button type="button" role="switch" aria-checked={exposed} aria-label={`Voice Assistant can control the ${entity.name} entity`}
      data-testid="voice-exposure" disabled={busy} onClick={() => onToggle(!exposed)}
      className="flex min-h-12 w-full items-center justify-between gap-3 py-1 text-left disabled:opacity-60">
      <span className="min-w-0">
        <span className="block truncate text-sm text-slate-200">{name}</span>
        <span className="block text-xs text-slate-500">{exposed ? 'Voice Assistant can control this' : 'Voice Assistant cannot control this'}
          {entity.voice && !entity.voice.chosen ? ' (default)' : ''}</span>
      </span>
      <SwitchTrack on={exposed} />
    </button>
  );
}

/**
 * One obvious device-level switch, "Voice Assistant can control this": on when every voiceable entity is exposed,
 * off when none is, mixed otherwise. The per-entity switches sit behind Details. Both draw the change at once and
 * roll back (with the hub's words) what the hub refused.
 */
function VoiceAssistantSection({ device, voiceable, onChanged }: {
  device: HardwareDetail; voiceable: HardwareEntity[]; onChanged: () => void;
}) {
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [details, setDetails] = useState(false);
  const detailsId = useId();
  const exposedOf = (e: HardwareEntity) => shown[e.entity_id] ?? Boolean(e.voice?.exposed);
  const on = voiceable.filter(exposedOf).length;
  const all = on === voiceable.length;
  const mixed = on > 0 && !all;

  const apply = async (ids: string[], voice: boolean) => {
    setBusy(true);
    setShown((prev) => ({ ...prev, ...Object.fromEntries(ids.map((id) => [id, voice])) }));
    const results = await Promise.allSettled(ids.map((id) => updateEntity(id, { voice })));
    const failed = ids.filter((_, i) => results[i].status === 'rejected');
    if (failed.length) {
      setShown((prev) => {
        const next = { ...prev };
        for (const id of failed) delete next[id];
        return next;
      });
      const first = results.find((r): r is PromiseRejectedResult => r.status === 'rejected');
      toast.error(failureText(first?.reason));
    }
    setBusy(false);
    if (failed.length < ids.length) onChanged();
  };

  return (
    <section aria-label="Voice Assistant" data-testid="voice-assistant" className={`${CARD} p-3`}>
      <button type="button" role="switch" aria-checked={mixed ? 'mixed' : all}
        aria-label={`Voice Assistant can control ${device.name}`} data-testid="voice-assistant-switch" disabled={busy}
        onClick={() => void apply(voiceable.map((e) => e.entity_id), !all)}
        className="flex min-h-12 w-full items-center justify-between gap-3 text-left disabled:opacity-60">
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-slate-100">Voice Assistant can control this</span>
          <span className="block text-xs text-slate-400" data-testid="voice-assistant-state">
            {mixed ? `Some parts: ${on} of ${voiceable.length}` : all ? 'On' : 'Off'}
          </span>
        </span>
        <SwitchTrack on={all} mixed={mixed} />
      </button>
      <button type="button" aria-expanded={details} aria-controls={detailsId} onClick={() => setDetails((v) => !v)}
        className="mt-1 inline-flex min-h-11 items-center gap-1 text-xs font-medium text-slate-400 hover:text-slate-200">
        <ChevronRight size={14} aria-hidden className={`motion-safe:transition-transform ${details ? 'rotate-90' : ''}`} />
        Details
      </button>
      <div id={detailsId} hidden={!details} data-testid="voice-access">
        <p className="text-xs font-medium text-slate-400">Voice Assistant, part by part</p>
        <div className="divide-y divide-white/5">
          {voiceable.map((e) => (
            <VoiceRow key={e.entity_id} entity={e} deviceName={device.name} exposed={exposedOf(e)} busy={busy}
              onToggle={(voice) => void apply([e.entity_id], voice)} />
          ))}
        </div>
      </div>
    </section>
  );
}

/**
 * How the hub reaches the device (its address and web password), for a device whose integration entry is its
 * own: changing them reconfigures the entry, which reloads it. A password is never shown, only whether one is set.
 */
function ConnectionSettings({ entry, onChanged }: { entry: HubEntry; onChanged: () => void }) {
  const fields = connectionFields(entry);
  const host = fields.find((f) => f.key === 'host');
  const secret = fields.find((f) => f.key === 'password');
  const [address, setAddress] = useState(host?.value ?? '');
  const [password, setPassword] = useState('');
  const passwordId = useId();
  const save = useMutation({
    mutationFn: () => reconfigureEntry(entry.entry_id, { data: {
      ...(host && address.trim() !== (host.value ?? '') ? { host: address.trim() } : {}),
      ...(secret && password ? { password } : {}),
    } }),
    onSuccess: () => { setPassword(''); toast.success('Saved. The hub is reconnecting to it.'); onChanged(); },
  });
  if (!fields.length) return null;
  const changed = Boolean(password) || (host !== undefined && address.trim() !== (host.value ?? ''));
  return (
    <form className="space-y-3 border-t border-white/5 pt-3" data-testid="connection-settings"
      onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <p className="text-xs font-medium text-slate-400">Connection</p>
      {host && (
        <label className="block text-xs text-slate-400">
          {host.label}
          <input className={`mt-1 font-mono ${INPUT}`} value={address} onChange={(e) => setAddress(e.target.value)}
            autoComplete="off" spellCheck={false} inputMode="url" />
          {host.help && <span className="mt-1 block text-[11px] text-slate-500">{host.help}</span>}
        </label>
      )}
      {secret && (
        // A div, not a wrapping label: the eye is a button, which must not join the label.
        <div className="block text-xs text-slate-400">
          <label htmlFor={passwordId}>{secret.label}</label>
          <PasswordField id={passwordId} wrapperClassName="mt-1" className={INPUT} value={password} onChange={(e) => setPassword(e.target.value)}
            placeholder={secret.set ? 'Unchanged' : 'Not set'} autoComplete="new-password"
            aria-describedby={entry.needs === 'password' ? `needs-${entry.entry_id}` : undefined} />
          {entry.needs === 'password' && (
            <span id={`needs-${entry.entry_id}`} className="mt-1 flex items-center gap-1 text-[11px] text-amber-200">
              <KeyRound size={12} aria-hidden /> The device asks for this password.
            </span>
          )}
          {secret.help && <span className="mt-1 block text-[11px] text-slate-500">{secret.help}</span>}
        </div>
      )}
      {save.error && <p className="text-sm text-amber-300" role="alert">{failureText(save.error)}</p>}
      <button type="submit" className={BTN} disabled={save.isPending || !changed}>
        {save.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null} Save connection
      </button>
    </form>
  );
}

function Info({ label, value }: { label: string; value: ReactNode }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-4 py-2">
      <dt className="shrink-0 text-xs text-slate-400">{label}</dt>
      <dd className="min-w-0 break-words text-right text-sm text-slate-200">{value}</dd>
    </div>
  );
}

function Body({ device, kind, areas, onChanged, onRemoved }: {
  device: HardwareDetail; kind: HardwareDevice['kind']; areas: { area_id: string; name: string }[];
  onChanged: () => void; onRemoved: () => void;
}) {
  const [name, setName] = useState(device.name_by_user || device.name);
  const labels = useQuery({ queryKey: LABELS_KEY, queryFn: getLabels, retry: false });
  const labelList = labels.data ?? [];
  const rename = useMutation({
    mutationFn: () => updateHubDevice(device.id, { name_by_user: name.trim() || null }),
    onSuccess: () => { toast.success('Name saved.'); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const move = useMutation({
    mutationFn: (area: string) => updateHubDevice(device.id, { area_id: area || null }),
    onSuccess: onChanged,
    onError: (err) => toast.error(failureText(err)),
  });
  const label = useMutation({
    mutationFn: (next: string[]) => updateHubDevice(device.id, { labels: next }),
    onSuccess: onChanged,
    onError: (err) => toast.error(failureText(err)),
  });
  const disable = useMutation({
    mutationFn: () => setDeviceDisabled(device.id, !device.disabled_by),
    onSuccess: () => { toast.success(device.disabled_by ? `${device.name} is turned back on.` : `${device.name} is turned off.`); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const remove = useMutation({
    mutationFn: (force: boolean) => removeHubDevice(device.id, force),
    onSuccess: (r) => { toast.success(r.warning ? `${device.name} was removed. ${r.warning}` : `${device.name} was removed.`); onRemoved(); },
    onError: async (err, force) => {
      if (failureStatus(err) !== 502 || force) { toast.error(failureText(err)); return; }
      // The device did not answer: the hub can still forget it.
      if (await confirmDialog({ title: `Remove ${device.name} anyway?`, body: failureText(err),
        confirmLabel: 'Remove anyway', danger: true })) remove.mutate(true);
    },
  });

  const bySection = useMemo(() => {
    const out: Record<EntitySection, HardwareEntity[]> = { controls: [], sensors: [], config: [], diagnostic: [], disabled: [] };
    for (const e of device.entity_list) out[entitySection(e)].push(e);
    return out;
  }, [device.entity_list]);
  const mainStates = [...bySection.controls, ...bySection.sensors].map((e) => e.state).filter((s): s is HubEntity => Boolean(s));
  const primary = primaryEntity(kind, mainStates);
  const offline = isOffline(device, primary);
  const allStates = device.entity_list.filter((e) => !e.disabled_by && e.state).map((e) => e.state as HubEntity);
  // What the main control already shows (a plug's power, a TV's remote, a vacuum's map) is not listed again.
  // An Echo's wake word sensitivity and wake-ups have their own section, with its warning.
  const wakeIds = wakeWordEntityIds(device.entity_list);
  const used = new Set([...(primary ? [primary.entity_id, ...usedSiblings(primary, allStates)] : []), ...wakeIds]);
  const diagnostics = bySection.diagnostic.filter((e) => !wakeIds.has(e.entity_id));
  const controls = bySection.controls.filter((e) => !used.has(e.entity_id));
  const sensors = bySection.sensors.filter((e) => !used.has(e.entity_id));
  const configs = bySection.config.filter((e) => !used.has(e.entity_id));
  const voiceable = device.entity_list.filter((e) => e.voice && !e.disabled_by);
  const ip = device.connections.find((c) => c.type === 'ip')?.value;
  const dim = offline ? 'pointer-events-none opacity-50' : '';

  return (
    <div className="space-y-6" data-testid="hardware-detail">
      {(offline || device.entry_reason) && (
        <div className="flex gap-3 rounded-2xl border border-rose-400/25 bg-rose-500/10 p-3" role="status" data-testid="offline-note">
          <WifiOff size={18} className="mt-0.5 shrink-0 text-rose-300" aria-hidden />
          <div className="min-w-0 text-sm">
            <p className="font-semibold text-rose-100">
              {device.status === 'disabled' ? 'Turned off in the hub'
                : (device.entry && entryNeedsText(device.entry)) || 'Not answering'}
            </p>
            <p className="mt-0.5 text-rose-100/80">
              {device.status === 'disabled'
                ? 'The hub ignores this device until it is turned back on under Settings.'
                : device.entry?.needs === 'password'
                  ? 'The device has a web password the hub does not have (or it was changed). Enter it under Settings, Connection.'
                  : device.entry_reason
                  || `Its controls work again once it reconnects. Check it has power and is on the network.${device.last_seen ? ` Last heard from ${ago(device.last_seen)}.` : ''}`}
            </p>
          </div>
        </div>
      )}

      {primary && (
        <div data-testid="main-control">
          <DeviceControl entity={primary} name={device.name} disabled={offline} siblings={allStates} />
        </div>
      )}

      {voiceable.length > 0 && <VoiceAssistantSection device={device} voiceable={voiceable} onChanged={onChanged} />}

      {controls.length > 0 && (
        <Section title="Controls" testId="section-controls">
          <div className={`${CARD} divide-y divide-white/5 px-3 ${dim}`}>
            {controls.map((e) => <EntityLine key={e.entity_id} entity={e} deviceName={device.name} />)}
          </div>
        </Section>
      )}
      {sensors.length > 0 && (
        <Section title="Readings" testId="section-sensors">
          <div className={`${CARD} divide-y divide-white/5 px-3`}>
            {sensors.map((e) => <EntityLine key={e.entity_id} entity={e} deviceName={device.name} />)}
          </div>
        </Section>
      )}
      {configs.length > 0 && (
        <Section title="Configuration" testId="section-config">
          <div className={`${CARD} divide-y divide-white/5 px-3 ${dim}`}>
            {configs.map((e) => <EntityLine key={e.entity_id} entity={e} deviceName={device.name} />)}
          </div>
        </Section>
      )}
      {wakeIds.size > 0 && (
        <div className={dim}><WakeWordSection entities={device.entity_list} onChanged={onChanged} /></div>
      )}

      <Section title="Settings" testId="section-settings">
        <div className={`${CARD} space-y-4 p-3`}>
          <form className="space-y-1" onSubmit={(e) => { e.preventDefault(); rename.mutate(); }}>
            <label htmlFor={`name-${device.id}`} className="text-xs font-medium text-slate-400">Name</label>
            <div className="flex gap-2">
              <input id={`name-${device.id}`} className={INPUT} value={name} placeholder={device.original_name || device.name}
                onChange={(e) => setName(e.target.value)} />
              <button type="submit" className={BTN} disabled={rename.isPending || name.trim() === (device.name_by_user || device.name)}>
                {rename.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null} Save
              </button>
            </div>
          </form>
          <label className="block text-xs font-medium text-slate-400">
            Room
            <select className={`mt-1 ${INPUT}`} value={device.area_id || ''} onChange={(e) => move.mutate(e.target.value)}
              aria-label={`Room of ${device.name}`} disabled={move.isPending}>
              <option value="">No room</option>
              {areas.map((a) => <option key={a.area_id} value={a.area_id}>{a.name}</option>)}
            </select>
          </label>
          <div>
            <p className="mb-1 text-xs font-medium text-slate-400">Labels</p>
            {labels.error ? <p className="text-xs text-amber-300/90">{failureText(labels.error)}</p> : (
              <LabelChips labels={labelList} selected={device.labels ?? []} name={device.name} disabled={label.isPending}
                onChange={(next) => label.mutate(next)} />
            )}
          </div>
          {device.entry && <ConnectionSettings key={device.entry.entry_id} entry={device.entry} onChanged={onChanged} />}
          <div className="flex flex-wrap gap-2 border-t border-white/5 pt-3">
            <button type="button" className={BTN} disabled={disable.isPending} onClick={() => disable.mutate()}>
              {disable.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Power size={14} aria-hidden />}
              {device.disabled_by ? 'Turn back on in the hub' : 'Turn off in the hub'}
            </button>
            {device.configuration_url && /^https?:/.test(device.configuration_url) && (
              <a href={device.configuration_url} target="_blank" rel="noopener noreferrer" className={BTN}>
                <ExternalLink size={14} aria-hidden /> Device&apos;s own page
              </a>
            )}
          </div>
        </div>
      </Section>

      {device.domain === 'esphome' && <RestartsSection deviceId={device.id} />}
      {device.domain === 'esphome' && <DeviceLogsSection deviceId={device.id} deviceName={device.name} />}
      {device.domain === 'tasmota' && device.entry_id && (
        <TasmotaTemplateSection entryId={device.entry_id} deviceName={device.name} />
      )}

      <details className={`${CARD} group`} data-testid="device-info">
        <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-2 px-3 text-sm font-semibold text-slate-300 [&::-webkit-details-marker]:hidden">
          Device info
          <ChevronRight size={16} className="text-slate-500 motion-safe:transition-transform group-open:rotate-90" aria-hidden />
        </summary>
        <div className="border-t border-white/5 px-3 pb-2">
          <dl className="divide-y divide-white/5">
            <Info label="Maker" value={device.manufacturer} />
            <Info label="Model" value={device.model} />
            <Info label="Firmware" value={device.sw_version} />
            <Info label="Hardware" value={device.hw_version} />
            <Info label="Serial number" value={device.serial_number} />
            <Info label="Added through" value={device.label ? `${device.label}${device.entry_title && device.entry_title !== device.name ? ` (${device.entry_title})` : ''}` : null} />
            <Info label="Connected through" value={device.via_device_name} />
            <Info label="Network address" value={ip ? <span className="font-mono text-xs">{ip}</span> : null} />
            {device.connections.filter((c) => c.type !== 'ip').map((c) => (
              <Info key={`${c.type}-${c.value}`} label={c.type === 'mac' ? 'MAC address' : c.type.charAt(0).toUpperCase() + c.type.slice(1)}
                value={<span className="font-mono text-xs">{c.value}</span>} />
            ))}
            <Info label="Last heard from" value={device.last_seen ? ago(device.last_seen) : null} />
            {device.children.length > 0 && <Info label="Connects" value={device.children.map((c) => c.name).join(', ')} />}
          </dl>
          {diagnostics.length > 0 && (
            <div className="border-t border-white/5" data-testid="section-diagnostic">
              <p className="pt-3 text-xs font-medium text-slate-400">Diagnostics</p>
              <div className="divide-y divide-white/5">
                {diagnostics.map((e) => <EntityLine key={e.entity_id} entity={e} deviceName={device.name} />)}
              </div>
            </div>
          )}
          {device.entity_list.length > 0 && (
            <div className="border-t border-white/5">
              <p className="pt-3 text-xs font-medium text-slate-400">Entities ({device.entity_list.length})</p>
              <ul className="divide-y divide-white/5">
                {device.entity_list.map((e) => (
                  <EntityAdminRow key={e.entity_id} entity={e} labels={labelList} deviceName={device.name} onChanged={onChanged} />
                ))}
              </ul>
            </div>
          )}
        </div>
      </details>

      <button type="button" disabled={remove.isPending}
        onClick={async () => {
          if (await confirmDialog({
            title: `Remove ${device.name}?`,
            body: `This removes it and its ${device.entity_list.length} entities from the hub. Automations that use them stop finding them.`,
            confirmLabel: 'Remove', danger: true,
          })) remove.mutate(false);
        }}
        className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-rose-500/30 px-3 text-sm font-semibold text-rose-200 hover:bg-rose-500/10 disabled:opacity-50">
        {remove.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Trash2 size={14} aria-hidden />} Remove device
      </button>
    </div>
  );
}

/**
 * One device: its control first, then its other controls and readings, then its settings, with
 * the technical facts folded away under Device info. `summary` is the tile's device, so the
 * header draws at once while the detail loads.
 */
export default function HubDeviceSheet({ deviceId, summary, areas, onClose }: {
  deviceId: string; summary?: HardwareDevice | null; areas: { area_id: string; name: string }[]; onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const detail = useQuery({ queryKey: ['homehub-hardware', deviceId], queryFn: () => getHardwareDevice(deviceId),
    retry: false, refetchInterval: 15_000 });
  const changed = () => {
    void queryClient.invalidateQueries({ queryKey: ['homehub-hardware'] });
    void queryClient.invalidateQueries({ queryKey: ['homehub-entries'] });
    void queryClient.invalidateQueries({ queryKey: ['dashboard', 'rooms'] });
  };
  const d = detail.data;
  const head = d ?? summary;
  const kind = summary?.kind ?? d?.kind;
  const states = (d?.entity_list ?? [])
    .filter((e) => !e.disabled_by && e.state && (entitySection(e) === 'controls' || entitySection(e) === 'sensors'))
    .map((e) => e.state as HubEntity);
  const primary = primaryEntity(kind, states);
  const tk = tileKind({ kind, name: head?.name ?? '', model: head?.model ?? null, manufacturer: head?.manufacturer ?? null }, primary);
  const offline = head ? isOffline(head, primary) : false;
  const line = head ? stateLine(head, primary) : '';
  const name = head?.name || 'Device';

  return (
    <DeviceSheet title={name} onClose={onClose} header={(
      <div className="flex min-w-0 items-center gap-3">
        <DeviceGlyph kind={tk} accent={accentFor(tk, primary)} active={!offline && isActiveState(primary)} size="lg" />
        <div className="min-w-0">
          <h2 className="line-clamp-2 break-words text-lg font-bold leading-tight text-white">{name}</h2>
          <p className={`mt-0.5 truncate text-sm ${offline ? 'text-rose-200' : 'text-slate-400'}`} data-testid="detail-status">
            {[head?.area || 'No room', line].filter(Boolean).join(' · ')}
          </p>
        </div>
      </div>
    )}>
      {detail.isLoading && <div className="space-y-3" role="status" aria-label="Loading the device">
        {[0, 1, 2].map((i) => <div key={i} className="skeleton h-14 w-full rounded-2xl" />)}
      </div>}
      {detail.error && <p className="text-sm text-amber-300/90" role="alert">{failureText(detail.error)}</p>}
      {d && <Body key={d.id} device={d} kind={kind} areas={areas} onChanged={changed} onRemoved={() => { changed(); onClose(); }} />}
    </DeviceSheet>
  );
}
