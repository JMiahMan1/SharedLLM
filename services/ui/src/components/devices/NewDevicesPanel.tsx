import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { ArrowRightLeft, EyeOff, Loader2, Network, Plus, Radar, RefreshCw, Undo2 } from 'lucide-react';
import AdoptDeviceDialog from './AdoptDeviceDialog';
import {
  addByAddress,
  adoptDevice,
  commissionMatter,
  getEntries,
  failureText,
  getDiscovered,
  getManualFlows,
  groupFlows,
  ignoreDevice,
  scanForDevices,
  updateAddress,
} from '../../lib/discoveredDevices';
import type { AdoptResult, DiscoveredDevice, DiscoveryInput, ManualFlow } from '../../lib/discoveredDevices';

const QUERY_KEY = ['homehub-discovered'];
const MATTER_CODE: DiscoveryInput = {
  key: 'code',
  label: 'Setup code',
  type: 'text',
  help: 'The QR code text (MT:...) or the 11 or 21 digit pairing code on the device or its box.',
};

function seen(iso: string | null): string {
  if (!iso) return '';
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 2) return 'seen just now';
  if (minutes < 90) return `seen ${minutes} min ago`;
  return `seen ${Math.round(minutes / 60)} h ago`;
}

/**
 * Devices > New devices (admins): what the device hub has found on the LAN that
 * is not set up yet, with Add and Ignore, a "Scan now", "Add Matter device"
 * for a Matter device that only shows up once its setup code is entered, and
 * "Add by address" for one that does not announce itself. Pairing steps (a
 * code on the TV, Allow on the TV, a HomeKit code) run in the Add dialog.
 */
export default function NewDevicesPanel() {
  const queryClient = useQueryClient();
  const [adopting, setAdopting] = useState<DiscoveredDevice | null>(null);
  const [matterOpen, setMatterOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [manual, setManual] = useState<ManualFlow | null>(null);
  const flows = useQuery({ queryKey: ['homehub-flows'], queryFn: getManualFlows, enabled: pickerOpen, retry: false,
    staleTime: 10 * 60 * 1000 });
  const [showIgnored, setShowIgnored] = useState(false);
  const list = useQuery({ queryKey: QUERY_KEY, queryFn: getDiscovered, retry: false, refetchInterval: 60_000 });
  // The rooms a new device can go in, from the same list the Integrations panel shows.
  const dialogOpen = Boolean(adopting || manual || matterOpen);
  const entries = useQuery({ queryKey: ['homehub-entries'], queryFn: getEntries, retry: false, enabled: dialogOpen });
  const areas = entries.data?.areas ?? [];
  const refresh = () => queryClient.invalidateQueries({ queryKey: QUERY_KEY });

  const scan = useMutation({
    mutationFn: scanForDevices,
    onSuccess: (data) => {
      queryClient.setQueryData(QUERY_KEY, data);
      if (data.scan?.skipped) toast('A scan ran a moment ago; showing its results.');
    },
    onError: (err) => toast.error(failureText(err)),
  });
  const ignore = useMutation({
    mutationFn: ({ id, ignored }: { id: string; ignored: boolean }) => ignoreDevice(id, ignored),
    onSuccess: () => void refresh(),
    onError: (err) => toast.error(failureText(err)),
  });

  const move = useMutation({
    mutationFn: (d: DiscoveredDevice) => updateAddress(d.id),
    onSuccess: (r, d) => {
      toast.success(`${d.moved?.entry_title || 'The device'} now uses ${r.new_host}.`);
      void refresh();
      void queryClient.invalidateQueries({ queryKey: ['homehub-entries'] });
      void queryClient.invalidateQueries({ queryKey: ['homehub-hardware'] });
    },
    onError: (err) => toast.error(failureText(err)),
  });

  const devices = list.data?.devices || [];
  const fresh = devices.filter((d) => d.new);
  const moved = devices.filter((d) => d.moved);
  const ignored = devices.filter((d) => d.ignored && !d.known);

  function added(result: AdoptResult) {
    toast.success(`${result.name || result.title || 'The device'} was added${result.entities?.length ? ` with ${result.entities.length} entities` : ''}.`);
    setAdopting(null);
    setMatterOpen(false);
    setManual(null);
    void refresh();
    void queryClient.invalidateQueries({ queryKey: ['homehub-entries'] });
    void queryClient.invalidateQueries({ queryKey: ['homehub-esphome-builder'] });
  }

  return (
    <>
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid="new-devices">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 bg-sky-500/5 px-4 py-3">
        <span className="flex min-w-0 items-center gap-2 text-[10px] font-black uppercase tracking-widest text-sky-400">
          <Radar size={14} /> New devices
        </span>
        <span className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setPickerOpen((v) => !v)}
            aria-expanded={pickerOpen}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5"
          >
            <Network size={14} /> Add by address
          </button>
          <button
            type="button"
            onClick={() => setMatterOpen(true)}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5"
          >
            <Plus size={14} /> Add Matter device
          </button>
          <button
            type="button"
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5 disabled:opacity-50"
          >
            {scan.isPending ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Scan now
          </button>
        </span>
      </header>

      {pickerOpen && (
        <div className="border-b border-white/5 px-4 py-3" data-testid="add-by-address">
          <p className="mb-2 text-xs text-slate-400">What kind of device?</p>
          {flows.isLoading && <p className="text-xs text-slate-500">Loading…</p>}
          {flows.error && <p className="text-xs text-amber-300/90">{failureText(flows.error)}</p>}
          <div className="space-y-3">
            {groupFlows(flows.data || []).map((g) => (
              <div key={g.kind}>
                <p className="mb-1 text-[10px] font-black uppercase tracking-widest text-slate-500">{g.label}</p>
                <div className="flex flex-wrap gap-2">
                  {g.flows.map((f) => (
                    <button
                      key={f.domain}
                      type="button"
                      onClick={() => { setManual(f); setPickerOpen(false); }}
                      className="min-h-11 rounded-lg border border-white/10 px-3 text-xs text-slate-200 hover:bg-white/5"
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {moved.length > 0 && (
        <div className="space-y-2 border-b border-white/5 bg-amber-500/5 px-4 py-3" data-testid="moved-devices">
          <p className="text-xs font-semibold text-amber-200">
            {moved.length === 1 ? 'A device has' : `${moved.length} devices have`} a new address
          </p>
          <ul className="space-y-2">
            {moved.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center justify-between gap-2" data-testid="moved-device">
                <span className="min-w-0 text-sm text-slate-200">
                  <span className="font-semibold">{d.moved?.entry_title}</span>
                  <span className="block text-xs text-slate-400">
                    {d.label} · {d.moved?.old_host || 'unknown'} → {d.moved?.new_host}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => move.mutate(d)}
                  disabled={move.isPending}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-amber-600 px-3 text-xs font-semibold text-white disabled:opacity-50"
                  aria-label={`Use the new address for ${d.moved?.entry_title}`}
                >
                  <ArrowRightLeft size={14} /> Use new address
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {list.isLoading && <p className="p-4 text-sm text-slate-500">Looking for devices…</p>}
      {list.error && <p className="p-4 text-sm text-amber-300/90" data-testid="new-devices-error">{failureText(list.error)}</p>}
      {list.data && fresh.length === 0 && (
        <p className="p-4 text-sm text-slate-500" data-testid="new-devices-empty">
          Nothing new on the network. Devices that are already set up are not listed.
        </p>
      )}

      <ul className="divide-y divide-white/5">
        {fresh.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3" data-testid="new-device">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-white">{d.name || d.host}</p>
              <p className="text-xs text-slate-400">
                {d.label}{d.model ? ` · ${d.model}` : ''}{d.host ? ` · ${d.host}` : ''}
                {d.last_seen ? ` · ${seen(d.last_seen)}` : ''}
              </p>
              {d.unsupported && <p className="mt-1 text-xs text-amber-300/90">{d.unsupported}</p>}
            </div>
            <div className="flex gap-2">
              {d.adoptable && (
                <button
                  type="button"
                  onClick={() => setAdopting(d)}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-sky-600 px-3 text-xs font-semibold text-white"
                  aria-label={`Add ${d.name || d.host}`}
                >
                  <Plus size={14} /> Add
                </button>
              )}
              <button
                type="button"
                onClick={() => ignore.mutate({ id: d.id, ignored: true })}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-slate-300 hover:bg-white/5"
                aria-label={`Ignore ${d.name || d.host}`}
              >
                <EyeOff size={14} /> Ignore
              </button>
            </div>
          </li>
        ))}
      </ul>

      {ignored.length > 0 && (
        <div className="border-t border-white/5 px-4 py-3">
          <button type="button" className="text-xs text-slate-400 underline" onClick={() => setShowIgnored((v) => !v)}>
            {showIgnored ? 'Hide' : 'Show'} ignored ({ignored.length})
          </button>
          {showIgnored && (
            <ul className="mt-2 space-y-1">
              {ignored.map((d) => (
                <li key={d.id} className="flex items-center justify-between gap-2 text-xs text-slate-400">
                  <span className="min-w-0 truncate">{d.name || d.host} · {d.label}</span>
                  <button
                    type="button"
                    onClick={() => ignore.mutate({ id: d.id, ignored: false })}
                    className="inline-flex min-h-9 items-center gap-1 rounded px-2 text-slate-300 hover:bg-white/5"
                    aria-label={`Stop ignoring ${d.name || d.host}`}
                  >
                    <Undo2 size={12} /> Un-ignore
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

    </section>
    {/* Outside the panel: its backdrop blur would otherwise be the fixed dialogs' containing block. */}
      {adopting && (
        <AdoptDeviceDialog
          title={`Add ${adopting.name || adopting.host}`}
          intro={`${adopting.label}${adopting.host ? ` at ${adopting.host}` : ''}`}
          needs={adopting.needs}
          areas={areas}
          onSubmit={({ name, areaId, input }) => adoptDevice(adopting.id, { name, area_id: areaId, input })}
          onDone={added}
          onClose={() => setAdopting(null)}
        />
      )}
      {manual && (
        <AdoptDeviceDialog
          title={`Add ${manual.label}`}
          intro="For a device that is on the network but was not found automatically."
          needs={manual.fields}
          areas={areas}
          onSubmit={({ name, areaId, input }) => addByAddress(manual.domain, { name, area_id: areaId, input, host: input.host, url: input.url })}
          onDone={added}
          onClose={() => setManual(null)}
        />
      )}
      {matterOpen && (
        <AdoptDeviceDialog
          title="Add a Matter device"
          intro="Put the device in pairing mode on the home network, then enter its setup code. Adding it can take a minute or two."
          needs={[MATTER_CODE]}
          areas={areas}
          onSubmit={async ({ name, areaId, input }) => commissionMatter(input.code || '', name, areaId)}
          onDone={added}
          onClose={() => setMatterOpen(false)}
        />
      )}
    </>
  );
}
