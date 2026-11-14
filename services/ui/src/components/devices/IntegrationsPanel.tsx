import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import {
  ChevronDown, ChevronRight, ExternalLink, Loader2, Pencil, Plug, Power, RefreshCw, RotateCw, Settings2, Trash2, Wrench,
} from 'lucide-react';
import Modal from '../ui/Modal';
import AdoptDeviceDialog from './AdoptDeviceDialog';
import {
  addByAddress,
  failureStatus,
  failureText,
  getBuilderLink,
  entriesNeedingAttention,
  getEntries,
  getManualFlows,
  offlineSince,
  reconfigureEntry,
  reloadEntry,
  removeEntry,
  removeHubDevice,
  setEntryDisabled,
  syncEntry,
  updateHubDevice,
} from '../../lib/discoveredDevices';
import type { HubArea, HubDevice, HubEntry } from '../../lib/discoveredDevices';

const ENTRIES_KEY = ['homehub-entries'];
const BUILDER_KEY = ['homehub-esphome-builder'];

type Removal = { kind: 'entry'; entry: HubEntry } | { kind: 'device'; entry: HubEntry; device: HubDevice };

function stateTone(state: string): string {
  if (state === 'loaded') return 'text-emerald-300';
  if (state === 'setup_retry' || state === 'setup_error') return 'text-amber-300';
  return 'text-slate-400';
}

/**
 * Devices > Integrations (admins): everything the device hub has set up, as
 * HA's "Devices & services". Each integration entry can be reconfigured (a new
 * address or login) and removed (HomeKit accessories are unpaired first); each
 * device can be renamed, moved to an area or removed. The ESPHome Device
 * Builder link shows here once its integration is set up.
 */
export default function IntegrationsPanel() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<HubEntry | null>(null);
  const [renaming, setRenaming] = useState<HubDevice | null>(null);
  const [removal, setRemoval] = useState<Removal | null>(null);
  const [builderSetup, setBuilderSetup] = useState(false);

  const list = useQuery({ queryKey: ENTRIES_KEY, queryFn: getEntries, retry: false, refetchInterval: 60_000 });
  const builder = useQuery({ queryKey: BUILDER_KEY, queryFn: getBuilderLink, retry: false });
  const builderFlow = useQuery({
    queryKey: ['homehub-flows'], queryFn: getManualFlows, enabled: builderSetup, retry: false, staleTime: 10 * 60 * 1000,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ENTRIES_KEY });
    void queryClient.invalidateQueries({ queryKey: BUILDER_KEY });
  };

  const move = useMutation({
    mutationFn: ({ device, area }: { device: HubDevice; area: string }) => updateHubDevice(device.id, { area_id: area || null }),
    onSuccess: () => refresh(),
    onError: (err) => toast.error(failureText(err)),
  });
  // HA's entry menu: reload, enable/disable, and (for integrations that find their own devices) sync.
  const entryAction = useMutation({
    mutationFn: ({ entry, action }: { entry: HubEntry; action: 'reload' | 'disable' | 'enable' | 'sync' }): Promise<unknown> => (
      action === 'reload' ? reloadEntry(entry.entry_id)
        : action === 'sync' ? syncEntry(entry.entry_id)
          : setEntryDisabled(entry.entry_id, action === 'disable')),
    onSuccess: (result, { entry, action }) => {
      const added = action === 'sync' ? (result as { entities?: string[] }).entities?.length ?? 0 : 0;
      toast.success(action === 'reload' ? `${entry.title} is reconnecting.`
        : action === 'sync' ? (added ? `${entry.title}: ${added} new entities.` : `${entry.title} has nothing new.`)
          : `${entry.title} is ${action === 'disable' ? 'disabled' : 'enabled'}.`);
      refresh();
      void queryClient.invalidateQueries({ queryKey: ['homehub-hardware'] });
    },
    onError: (err) => toast.error(failureText(err)),
  });

  const areas = list.data?.areas || [];
  const areaName = (id: string | null) => areas.find((a) => a.area_id === id)?.name || '';
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const all = list.data?.entries || [];
    if (!q) return all;
    return all.filter((e) => [e.label, e.title, ...e.devices.map((d) => d.name)].some((t) => t.toLowerCase().includes(q)));
  }, [list.data, filter]);
  const builderFields = builderFlow.data?.find((f) => f.domain === 'esphome_builder')?.fields;

  return (
    <>
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid="integrations">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 bg-violet-500/5 px-4 py-3">
        <span className="flex min-w-0 items-center gap-2 text-[10px] font-black uppercase tracking-widest text-violet-300">
          <Plug size={14} /> Integrations
        </span>
        <span className="flex flex-wrap items-center gap-2">
          {builder.data?.configured && builder.data.url ? (
            <a
              href={builder.data.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5"
              data-testid="esphome-builder-link"
            >
              <Wrench size={14} /> ESPHome Builder <ExternalLink size={12} />
            </a>
          ) : builder.data && !builder.data.configured ? (
            <button
              type="button"
              onClick={() => setBuilderSetup(true)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-300 hover:bg-white/5"
            >
              <Wrench size={14} /> Set up ESPHome Builder
            </button>
          ) : null}
          <input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter"
            aria-label="Filter integrations"
            className="min-h-11 w-40 rounded-lg border border-white/10 bg-black/30 px-3 text-xs text-white"
          />
        </span>
      </header>

      {list.data && (() => {
        const down = entriesNeedingAttention(list.data.entries);
        if (!list.data.entries.length) return null;
        return down.length === 0 ? (
          <p className="px-4 pt-3 text-xs text-emerald-300" role="status" data-testid="integrations-health">
            All {list.data.entries.length} connections are working.
          </p>
        ) : (
          <div className="px-4 pt-3" role="status" data-testid="integrations-health">
            <p className="text-xs font-semibold text-amber-200">
              {down.length} connection{down.length === 1 ? ' needs' : 's need'} attention
            </p>
            {/* What is not answering and since when, so one that is gone for good can be removed here. */}
            <ul className="mt-2 divide-y divide-amber-500/10 rounded-xl border border-amber-500/30 bg-amber-500/5" data-testid="offline-entries">
              {down.map((entry) => (
                <li key={entry.entry_id} className="flex min-w-0 flex-wrap items-center gap-2 px-3 py-2" data-testid="offline-entry">
                  <button
                    type="button"
                    onClick={() => setOpen((o) => ({ ...o, [entry.entry_id]: true }))}
                    className="flex min-h-11 min-w-0 flex-1 flex-col justify-center text-left"
                    title={entry.reason || undefined}
                    aria-label={`${entry.title}: ${entry.reason || entry.state.replace('_', ' ')}`}
                  >
                    <span className="block truncate text-sm font-semibold text-amber-100">{entry.title}</span>
                    <span className="block truncate text-xs text-slate-400">
                      {[entry.label, offlineSince(entry.offline_since)].filter(Boolean).join(' · ')}
                    </span>
                  </button>
                  {!entry.shared && (
                    <button
                      type="button"
                      onClick={() => setRemoval({ kind: 'entry', entry })}
                      className="inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg border border-rose-500/30 px-3 text-xs text-rose-200 hover:bg-rose-500/10"
                      aria-label={`Remove ${entry.title}, it is not answering`}
                    >
                      <Trash2 size={14} /> {entry.unpairs ? 'Unpair' : 'Remove'}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })()}
      {list.isLoading && <div className="space-y-2 p-4" role="status" aria-label="Loading integrations">
        {[0, 1, 2].map((i) => <div key={i} className="skeleton h-12 w-full rounded-xl" />)}
      </div>}
      {list.error && <p className="p-4 text-sm text-amber-300/90">{failureText(list.error)}</p>}

      <ul className="divide-y divide-white/5">
        {shown.map((entry) => {
          const isOpen = open[entry.entry_id] || Boolean(filter.trim());
          return (
            <li key={entry.entry_id} className="px-4 py-3" data-testid="integration">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <button
                  type="button"
                  className="flex min-h-11 min-w-0 items-center gap-2 text-left"
                  onClick={() => setOpen((o) => ({ ...o, [entry.entry_id]: !o[entry.entry_id] }))}
                  aria-expanded={isOpen}
                >
                  {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-semibold text-white">{entry.title}</span>
                    <span className="block text-xs text-slate-400">
                      {entry.label} · {entry.devices.length} device{entry.devices.length === 1 ? '' : 's'} ·{' '}
                      <span className={stateTone(entry.state)}>{entry.disabled_by ? 'disabled' : entry.state.replace('_', ' ')}</span>
                    </span>
                  </span>
                </button>
                <span className="flex flex-wrap gap-2">
                  {!entry.disabled_by && (
                    <button
                      type="button"
                      onClick={() => entryAction.mutate({ entry, action: 'reload' })}
                      className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5"
                      aria-label={`Reload ${entry.title}`}
                    >
                      <RotateCw size={14} /> Reload
                    </button>
                  )}
                  {entry.syncs && entry.state === 'loaded' && !entry.disabled_by && (
                    <button
                      type="button"
                      onClick={() => entryAction.mutate({ entry, action: 'sync' })}
                      className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5"
                      aria-label={`Sync devices of ${entry.title}`}
                    >
                      <RefreshCw size={14} /> Sync devices
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => entryAction.mutate({ entry, action: entry.disabled_by ? 'enable' : 'disable' })}
                    className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5"
                    aria-label={`${entry.disabled_by ? 'Enable' : 'Disable'} ${entry.title}`}
                  >
                    <Power size={14} /> {entry.disabled_by ? 'Enable' : 'Disable'}
                  </button>
                  {entry.reconfigure.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setEditing(entry)}
                      className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5"
                      aria-label={`Reconfigure ${entry.title}`}
                    >
                      <Settings2 size={14} /> Reconfigure
                    </button>
                  )}
                  {!entry.shared && (
                    <button
                      type="button"
                      onClick={() => setRemoval({ kind: 'entry', entry })}
                      className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-rose-500/30 px-3 text-xs text-rose-200 hover:bg-rose-500/10"
                      aria-label={`Remove ${entry.title}`}
                    >
                      <Trash2 size={14} /> {entry.unpairs ? 'Unpair' : 'Remove'}
                    </button>
                  )}
                </span>
              </div>
              {entry.reason && entry.state !== 'loaded' && <p className="mt-1 text-xs text-amber-300/80">{entry.reason}</p>}
              {isOpen && (
                <ul className="mt-2 space-y-2 pl-5">
                  {entry.devices.length === 0 && <li className="text-xs text-slate-500">No devices.</li>}
                  {entry.devices.map((d) => (
                    <li key={d.id} className="flex flex-wrap items-center justify-between gap-2" data-testid="hub-device">
                      <div className="min-w-0">
                        <p className="truncate text-sm text-slate-200">{d.name}</p>
                        <p className="text-xs text-slate-500">
                          {[d.manufacturer, d.model].filter(Boolean).join(' ')}
                          {d.entities.length ? ` · ${d.entities.length} entities` : ''}
                        </p>
                      </div>
                      <span className="flex flex-wrap items-center gap-2">
                        <select
                          value={d.area_id || ''}
                          onChange={(e) => move.mutate({ device: d, area: e.target.value })}
                          aria-label={`Area of ${d.name}`}
                          className="min-h-11 rounded-lg border border-white/10 bg-black/30 px-2 text-xs text-white"
                        >
                          <option value="">No area</option>
                          {areas.map((a: HubArea) => <option key={a.area_id} value={a.area_id}>{a.name}</option>)}
                          {d.area_id && !areaName(d.area_id) && <option value={d.area_id}>{d.area_id}</option>}
                        </select>
                        <button
                          type="button"
                          onClick={() => setRenaming(d)}
                          className="inline-flex min-h-11 items-center gap-1 rounded-lg px-2 text-xs text-slate-300 hover:bg-white/5"
                          aria-label={`Rename ${d.name}`}
                        >
                          <Pencil size={13} /> Rename
                        </button>
                        {(entry.shared || entry.devices.length > 1) && (
                          <button
                            type="button"
                            onClick={() => setRemoval({ kind: 'device', entry, device: d })}
                            className="inline-flex min-h-11 items-center gap-1 rounded-lg px-2 text-xs text-rose-200 hover:bg-rose-500/10"
                            aria-label={`Remove device ${d.name}`}
                          >
                            <Trash2 size={13} /> Remove
                          </button>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>

    </section>
    {/* Outside the panel: its backdrop blur would otherwise be the fixed dialogs' containing block. */}
      {editing && <ReconfigureDialog entry={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />}
      {renaming && <RenameDialog device={renaming} onClose={() => setRenaming(null)} onSaved={() => { setRenaming(null); refresh(); }} />}
      {removal && <RemoveDialog removal={removal} onClose={() => setRemoval(null)} onRemoved={() => { setRemoval(null); refresh(); }} />}
      {builderSetup && builderFields && (
        <AdoptDeviceDialog
          title="Set up the ESPHome Builder"
          intro="Where admins open the ESPHome Device Builder (it compiles and flashes ESPHome firmware)."
          needs={builderFields}
          submitLabel="Save"
          onSubmit={({ name, input }) => addByAddress('esphome_builder', { name, input, url: input.url })}
          onDone={() => { setBuilderSetup(false); refresh(); toast.success('The ESPHome Builder link is set up.'); }}
          onClose={() => setBuilderSetup(false)}
        />
      )}
    </>
  );
}

function ReconfigureDialog({ entry, onClose, onSaved }: { entry: HubEntry; onClose: () => void; onSaved: () => void }) {
  const [title, setTitle] = useState(entry.title);
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(entry.reconfigure.map((f) => [f.key, f.value ?? ''])),
  );
  const save = useMutation({
    mutationFn: () => {
      const data = Object.fromEntries(
        entry.reconfigure
          .filter((f) => (f.type === 'password' ? Boolean(values[f.key]) : values[f.key] !== (f.value ?? '')))
          .map((f) => [f.key, values[f.key]]),
      );
      return reconfigureEntry(entry.entry_id, { ...(title.trim() !== entry.title ? { title: title.trim() } : {}), data });
    },
    onSuccess: () => { toast.success(`${entry.title} was updated and is reconnecting.`); onSaved(); },
  });
  return (
    <Modal isOpen onClose={onClose} title={`Reconfigure ${entry.title}`} size="md">
      <form className="space-y-3" data-testid="reconfigure-dialog" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <label className="block text-xs text-slate-400">
          Name
          <input className="mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"
            value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        {entry.reconfigure.map((f) => (
          <label key={f.key} className="block text-xs text-slate-400">
            {f.label}
            <input
              className="mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-sm text-white"
              type={f.type === 'password' ? 'password' : 'text'}
              value={values[f.key] || ''}
              placeholder={f.type === 'password' && f.set ? 'Unchanged' : undefined}
              onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
        ))}
        {save.error && <p className="text-sm text-amber-300" role="alert">{failureText(save.error)}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="min-h-11 rounded-lg px-4 text-sm text-slate-300 hover:bg-white/5">Cancel</button>
          <button type="submit" disabled={save.isPending} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-sky-600 px-4 text-sm font-semibold text-white disabled:opacity-50">
            {save.isPending && <Loader2 size={14} className="animate-spin" />} Save
          </button>
        </div>
      </form>
    </Modal>
  );
}

function RenameDialog({ device, onClose, onSaved }: { device: HubDevice; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(device.name_by_user || '');
  const save = useMutation({
    mutationFn: () => updateHubDevice(device.id, { name_by_user: name.trim() || null }),
    onSuccess: onSaved,
  });
  return (
    <Modal isOpen onClose={onClose} title={`Rename ${device.name}`} size="sm">
      <form className="space-y-3" data-testid="rename-dialog" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <label className="block text-xs text-slate-400">
          Name
          <input className="mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-white"
            value={name} placeholder={device.original_name || ''} onChange={(e) => setName(e.target.value)} autoFocus />
          <span className="mt-1 block text-[11px] text-slate-500">Leave empty to use the device's own name.</span>
        </label>
        {save.error && <p className="text-sm text-amber-300" role="alert">{failureText(save.error)}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="min-h-11 rounded-lg px-4 text-sm text-slate-300 hover:bg-white/5">Cancel</button>
          <button type="submit" disabled={save.isPending} className="min-h-11 rounded-lg bg-sky-600 px-4 text-sm font-semibold text-white disabled:opacity-50">Save</button>
        </div>
      </form>
    </Modal>
  );
}

function RemoveDialog({ removal, onClose, onRemoved }: { removal: Removal; onClose: () => void; onRemoved: () => void }) {
  const [unreachable, setUnreachable] = useState<string | null>(null);
  const name = removal.kind === 'entry' ? removal.entry.title : removal.device.name;
  const remove = useMutation({
    mutationFn: (force: boolean) => (removal.kind === 'entry'
      ? removeEntry(removal.entry.entry_id, force)
      : removeHubDevice(removal.device.id, force)),
    onSuccess: (result) => {
      toast.success(result.warning ? `${name} was removed. ${result.warning}` : `${name} was removed.`);
      onRemoved();
    },
    onError: (err) => {
      // 502: the device could not be reached to unpair; the admin may remove it anyway.
      if (failureStatus(err) === 502) setUnreachable(failureText(err));
    },
  });
  const unpairs = removal.entry.unpairs && (removal.kind === 'entry' || removal.entry.devices.length === 1);
  return (
    <Modal isOpen onClose={onClose} title={`Remove ${name}?`} size="sm">
      <div className="space-y-3" data-testid="remove-dialog">
        <p className="text-sm text-slate-300">
          {removal.kind === 'entry'
            ? `This removes the ${removal.entry.label} integration "${removal.entry.title}" with its ${removal.entry.devices.length} device(s) and ${removal.entry.entities} entities.`
            : `This removes the device and its ${removal.device.entities.length} entities.`}
          {unpairs ? ' The accessory is unpaired, so it can be paired again.' : ''}
          {' '}Automations that use them will stop finding them.
        </p>
        {unreachable && <p className="text-sm text-amber-300" role="alert">{unreachable}</p>}
        {remove.error && !unreachable && <p className="text-sm text-amber-300" role="alert">{failureText(remove.error)}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="min-h-11 rounded-lg px-4 text-sm text-slate-300 hover:bg-white/5">Cancel</button>
          <button
            type="button"
            disabled={remove.isPending}
            onClick={() => remove.mutate(Boolean(unreachable))}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-rose-600 px-4 text-sm font-semibold text-white disabled:opacity-50"
          >
            {remove.isPending && <Loader2 size={14} className="animate-spin" />} {unreachable ? 'Remove anyway' : 'Remove'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
