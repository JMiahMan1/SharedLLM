import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { EyeOff, Loader2, Plus, Radar, RefreshCw, Undo2 } from 'lucide-react';
import AdoptDeviceDialog from './AdoptDeviceDialog';
import {
  adoptDevice,
  commissionMatter,
  failureText,
  getDiscovered,
  ignoreDevice,
  scanForDevices,
} from '../../lib/discoveredDevices';
import type { AdoptResult, DiscoveredDevice, DiscoveryInput } from '../../lib/discoveredDevices';

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
 * is not set up yet, with Add and Ignore, a "Scan now", and "Add Matter device"
 * for a Matter device that only shows up once its setup code is entered.
 */
export default function NewDevicesPanel() {
  const queryClient = useQueryClient();
  const [adopting, setAdopting] = useState<DiscoveredDevice | null>(null);
  const [matterOpen, setMatterOpen] = useState(false);
  const [showIgnored, setShowIgnored] = useState(false);
  const list = useQuery({ queryKey: QUERY_KEY, queryFn: getDiscovered, retry: false, refetchInterval: 60_000 });
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

  const devices = list.data?.devices || [];
  const fresh = devices.filter((d) => d.new);
  const ignored = devices.filter((d) => d.ignored && !d.known);

  function added(result: AdoptResult) {
    toast.success(`${result.name || result.title || 'The device'} was added${result.entities?.length ? ` with ${result.entities.length} entities` : ''}.`);
    setAdopting(null);
    setMatterOpen(false);
    void refresh();
  }

  return (
    <section className="glass-panel overflow-hidden rounded-2xl border border-white/10" data-testid="new-devices">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 bg-sky-500/5 px-4 py-3">
        <span className="flex min-w-0 items-center gap-2 text-[10px] font-black uppercase tracking-widest text-sky-400">
          <Radar size={14} /> New devices
        </span>
        <span className="flex flex-wrap gap-2">
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

      {adopting && (
        <AdoptDeviceDialog
          title={`Add ${adopting.name || adopting.host}`}
          intro={`${adopting.label}${adopting.host ? ` at ${adopting.host}` : ''}`}
          needs={adopting.needs}
          onSubmit={({ name, input }) => adoptDevice(adopting.id, { name, input })}
          onDone={added}
          onClose={() => setAdopting(null)}
        />
      )}
      {matterOpen && (
        <AdoptDeviceDialog
          title="Add a Matter device"
          intro="Put the device in pairing mode on the home network, then enter its setup code. Adding it can take a minute or two."
          needs={[MATTER_CODE]}
          onSubmit={async ({ name, input }) => commissionMatter(input.code || '', name)}
          onDone={added}
          onClose={() => setMatterOpen(false)}
        />
      )}
    </section>
  );
}
