import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Loader2, MapPin, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { failureText } from '../../lib/discoveredDevices';
import {
  ZONE_SOURCE_LABEL, createZone, deleteZone, getZones, parseLatLon, syncZones, updateZone, zoneBody, zoneForm,
  type Zone, type ZoneForm,
} from '../../lib/homeSetup';
import { confirmDialog } from '../ui/confirmDialog';
import SetupSection from './SetupSection';
import { BTN, DANGER, INPUT } from './setupStyles';

const ZONES = ['homehub-zones'];

/** Name, where (latitude and longitude, or one "lat, lon" pasted from a map), how far, and passive. */
function ZoneEditor({ initial, submitLabel, busy, onSubmit, onCancel }: {
  initial: ZoneForm; submitLabel: string; busy: boolean; onSubmit: (form: ZoneForm) => void; onCancel?: () => void;
}) {
  const [form, setForm] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const set = (patch: Partial<ZoneForm>) => { setForm((f) => ({ ...f, ...patch })); setProblem(null); };
  const coord = (key: 'latitude' | 'longitude', value: string) => {
    const pair = parseLatLon(value);
    if (pair) set({ latitude: String(pair.latitude), longitude: String(pair.longitude) });
    else set({ [key]: value });
  };
  return (
    <form className="grid grid-cols-1 gap-2 sm:grid-cols-2" data-testid="zone-editor" onSubmit={(e) => {
      e.preventDefault();
      const out = zoneBody(form);
      if ('error' in out) setProblem(out.error); else onSubmit(form);
    }}>
      <label className="text-xs text-slate-400 sm:col-span-2">Name
        <input className={INPUT} value={form.name} onChange={(e) => set({ name: e.target.value })} />
      </label>
      <label className="text-xs text-slate-400">Latitude
        <input className={INPUT} value={form.latitude} inputMode="decimal" placeholder="or paste lat, lon"
          onChange={(e) => coord('latitude', e.target.value)} />
      </label>
      <label className="text-xs text-slate-400">Longitude
        <input className={INPUT} value={form.longitude} inputMode="decimal" onChange={(e) => coord('longitude', e.target.value)} />
      </label>
      <label className="text-xs text-slate-400">Radius (metres)
        <input className={INPUT} value={form.radius} inputMode="numeric" onChange={(e) => set({ radius: e.target.value })} />
      </label>
      <label className="flex min-h-11 items-center gap-2 self-end text-xs text-slate-300">
        <input type="checkbox" className="h-11 w-5 shrink-0" checked={form.passive} onChange={(e) => set({ passive: e.target.checked })} />
        Passive (only for automations, never someone&apos;s location)
      </label>
      {problem && <p className="text-xs text-amber-300 sm:col-span-2" role="alert">{problem}</p>}
      <div className="flex flex-wrap gap-2 sm:col-span-2">
        <button type="submit" className={BTN} disabled={busy}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} {submitLabel}
        </button>
        {onCancel && <button type="button" className={BTN} onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}

function ZoneRow({ zone, onChanged }: { zone: Zone; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const save = useMutation({
    mutationFn: (form: ZoneForm) => {
      const out = zoneBody(form);
      if ('error' in out) throw new Error(out.error);
      return updateZone(zone.id, out.body);
    },
    onSuccess: () => { setEditing(false); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const remove = useMutation({
    mutationFn: () => deleteZone(zone.id),
    onSuccess: () => { toast.success(`${zone.name} was removed.`); onChanged(); },
    onError: (err) => toast.error(failureText(err)),
  });
  return (
    <li className="space-y-2 px-4 py-3" data-testid="zone">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-white">{zone.name}</p>
          <p className="text-xs text-slate-400">
            {ZONE_SOURCE_LABEL[zone.source]}
            {zone.radius != null && ` · ${Math.round(zone.radius)} m`}
            {zone.passive && ' · passive'}
          </p>
          {zone.entity_id && <p className="break-all font-mono text-[11px] text-slate-500">{zone.entity_id}</p>}
        </div>
        {zone.editable && !editing && (
          <span className="flex gap-2">
            <button type="button" className={BTN} onClick={() => setEditing(true)} aria-label={`Edit ${zone.name}`}>
              <Pencil size={13} /> Edit
            </button>
            <button type="button" className={DANGER} disabled={remove.isPending} aria-label={`Remove ${zone.name}`}
              onClick={async () => {
                if (await confirmDialog({ title: `Remove ${zone.name}?`,
                  body: 'Automations that use this zone stop finding it.', confirmLabel: 'Remove', danger: true })) {
                  remove.mutate();
                }
              }}>
              <Trash2 size={13} />
            </button>
          </span>
        )}
      </div>
      {editing && (
        <ZoneEditor initial={zoneForm(zone)} submitLabel="Save" busy={save.isPending}
          onSubmit={(form) => save.mutate(form)} onCancel={() => setEditing(false)} />
      )}
    </li>
  );
}

/**
 * Zones: places automations can react to (arriving, leaving, being inside). Drawn here, plus home and the
 * family's shared saved places from Jarvis, which are shown but changed where they live.
 */
export default function ZonesPanel() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: ZONES, queryFn: getZones, retry: false });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ZONES });
  const [adding, setAdding] = useState(false);
  const add = useMutation({
    mutationFn: (form: ZoneForm) => {
      const out = zoneBody(form);
      if ('error' in out) throw new Error(out.error);
      return createZone(out.body);
    },
    onSuccess: (z) => { toast.success(`${z.name} was added.`); setAdding(false); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const sync = useMutation({
    mutationFn: () => syncZones(),
    onSuccess: (r) => { toast.success(`${r.places} saved place${r.places === 1 ? '' : 's'} followed.`); refresh(); },
    onError: (err) => toast.error(failureText(err)),
  });
  const zones = list.data ?? [];
  return (
    <SetupSection title="Zones" icon={MapPin} testId="zones" aside={
      <button type="button" className={BTN} disabled={sync.isPending} onClick={() => sync.mutate()}>
        {sync.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Saved places
      </button>
    }>
      {list.isLoading && <p className="p-4 text-sm text-slate-500">Loading zones…</p>}
      {list.error && <p className="p-4 text-sm text-amber-300/90" role="alert">{failureText(list.error)}</p>}
      <ul className="divide-y divide-white/5">{zones.map((z) => <ZoneRow key={z.id} zone={z} onChanged={refresh} />)}</ul>
      <div className="border-t border-white/5 px-4 py-3">
        {adding ? (
          <ZoneEditor initial={zoneForm()} submitLabel="Add zone" busy={add.isPending}
            onSubmit={(form) => add.mutate(form)} onCancel={() => setAdding(false)} />
        ) : (
          <button type="button" className={BTN} onClick={() => setAdding(true)}><Plus size={14} /> New zone</button>
        )}
      </div>
    </SetupSection>
  );
}
