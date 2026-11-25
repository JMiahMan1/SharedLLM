import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUpCircle, CheckCircle2, Loader2, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { confirmDialog } from '../ui/confirmDialog';
import { firmwareLine, firmwareStatus, type FirmwareRow } from '../../lib/firmware';

/**
 * Admin > Firmware: one list of every device that reports a version.
 *
 * HomeHub publishes update entities for the integrations that can see an
 * offer (ESPHome, Reolink, Echo), and a device that only knows its own version
 * (Tasmota) gets a row that fetches from its own update address. An offer is
 * only ever shown when the version is newer than the one installed, so nothing
 * here can walk a device back a release.
 */

const BADGES: Record<ReturnType<typeof firmwareStatus>, { text: string; className: string }> = {
  busy: { text: 'Installing', className: 'bg-sky-500/15 text-sky-200 border-sky-400/30' },
  available: { text: 'Update available', className: 'bg-emerald-500/15 text-emerald-200 border-emerald-400/30' },
  current: { text: 'Up to date', className: 'bg-white/5 text-slate-300 border-white/10' },
  manual: { text: 'Updated from its own address', className: 'bg-amber-500/10 text-amber-200 border-amber-400/30' },
  unknown: { text: 'Version unknown', className: 'bg-white/5 text-slate-400 border-white/10' },
};

function Tile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="glass-panel rounded-xl p-3">
      <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-black text-white">{value}</div>
    </div>
  );
}

export function FirmwarePanel() {
  const queryClient = useQueryClient();
  const { data, isPending, isError, error, isFetching, refetch } = useQuery({
    queryKey: ['firmware'],
    queryFn: () => api.getFirmware(),
    refetchInterval: (query) => ((query.state.data?.installing ?? 0) > 0 ? 5000 : 30000),
  });

  const rows = data?.devices ?? [];
  const offered = rows.filter((r) => r.available);
  const busy = data?.installing ?? 0;

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['firmware'] });
    refetch();
  };

  const install = useMutation({
    mutationFn: (row: FirmwareRow) => api.installFirmware(row.entity_id ?? row.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['firmware'] });
      toast.success('The update has started');
    },
    onError: (e) => toast.error(errorText(e, 'Could not start that update')),
  });

  const upgrade = useMutation({
    mutationFn: (row: FirmwareRow) => api.upgradeDeviceFirmware(row.device_id ?? row.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['firmware'] });
      toast.success('It is fetching its own firmware and will restart');
    },
    onError: (e) => toast.error(errorText(e, 'Could not ask that device to update')),
  });

  const updateAll = useMutation({
    mutationFn: async (list: FirmwareRow[]) => {
      const failed: string[] = [];
      // One at a time: several integrations compiling and flashing at once is
      // how a house loses its lights for an evening.
      for (const row of list) {
        try {
          await api.installFirmware(row.entity_id ?? row.id);
        } catch {
          failed.push(row.device ?? row.name);
        }
      }
      return failed;
    },
    onSuccess: (failed) => {
      queryClient.invalidateQueries({ queryKey: ['firmware'] });
      if (failed.length) toast.error(`${failed.length} could not start: ${failed.join(', ')}`);
      else toast.success('Every update has started');
    },
    onError: (e) => toast.error(errorText(e, 'Could not start the updates')),
  });

  const askInstall = async (row: FirmwareRow) => {
    const ok = await confirmDialog({
      title: `Update ${row.device ?? row.name}?`,
      body: `It will install ${row.latest_version ?? 'the new version'} and restart, so it will be away for a moment.`,
      confirmLabel: 'Update',
    });
    if (ok) install.mutate(row);
  };

  const askUpgrade = async (row: FirmwareRow) => {
    const ok = await confirmDialog({
      title: `Update ${row.device ?? row.name}?`,
      body: 'It fetches the image from its own update address and restarts. If the address is not set on the device, nothing happens.',
      confirmLabel: 'Update',
    });
    if (ok) upgrade.mutate(row);
  };

  const askAll = async () => {
    const ok = await confirmDialog({
      title: `Update ${offered.length} device${offered.length === 1 ? '' : 's'}?`,
      body: 'Each one installs its new version and restarts, one after another. This can take a few minutes.',
      confirmLabel: 'Update all',
    });
    if (ok) updateAll.mutate(offered.filter((r) => r.installable));
  };

  return (
    <section className="space-y-6" data-testid="firmware-panel">
      <div className="glass-panel p-4 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-lg font-black text-white">
              <ArrowUpCircle size={20} className="text-emerald-300" aria-hidden /> Firmware
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-slate-400">
              Every device that reports its version. An update is only offered when it is newer than what the device
              runs now, so nothing here can put an older release back on.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={refresh}
              disabled={isFetching}
              className="glass-button flex min-h-11 items-center gap-2 px-3 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
            >
              <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} aria-hidden /> Refresh
            </button>
            {offered.some((r) => r.installable) && (
              <button
                type="button"
                onClick={askAll}
                disabled={updateAll.isPending}
                data-testid="firmware-update-all"
                className="glass-button flex min-h-11 items-center gap-2 px-3 text-[10px] font-black uppercase tracking-widest text-emerald-200 disabled:opacity-50"
              >
                <ArrowUpCircle size={14} aria-hidden /> Update all ({offered.filter((r) => r.installable).length})
              </button>
            )}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Tile label="Devices" value={rows.length} />
          <Tile label="Updates offered" value={offered.length} />
          <Tile label="Installing" value={busy} />
        </div>
      </div>

      {isError && (
        <div className="glass-panel p-4 text-sm text-rose-200">{errorText(error, 'Could not reach HomeHub')}</div>
      )}

      {isPending ? (
        <div className="glass-panel flex items-center gap-2 p-4 text-sm text-slate-400">
          <Loader2 size={16} className="animate-spin" aria-hidden /> Reading what every device runs
        </div>
      ) : rows.length === 0 ? (
        <div className="glass-panel p-6 text-center">
          <CheckCircle2 size={28} className="mx-auto text-slate-500" aria-hidden />
          <p className="mt-2 text-sm text-slate-400">
            No device reports a version yet. A device appears here once its integration has adopted it.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {rows.map((row) => {
            const status = firmwareStatus(row);
            const badge = BADGES[status];
            const working =
              (install.isPending && install.variables?.id === row.id) ||
              (upgrade.isPending && upgrade.variables?.id === row.id);
            return (
              <div
                key={row.id}
                data-testid={`firmware-row-${row.id}`}
                className="glass-panel flex flex-wrap items-center gap-3 p-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate font-semibold text-white">{row.device ?? row.name}</span>
                    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-black uppercase tracking-widest ${badge.className}`}>
                      {status === 'busy' && row.progress != null ? `Installing ${Math.round(row.progress)}%` : badge.text}
                    </span>
                  </div>
                  <div className="truncate text-xs text-slate-400">
                    {[row.integration, row.model].filter(Boolean).join(' · ') || 'Unknown integration'}
                  </div>
                  <div className="mt-1 font-mono text-xs text-slate-300">{firmwareLine(row)}</div>
                </div>

                <div className="flex shrink-0 gap-2">
                  {row.available && row.installable && (
                    <button
                      type="button"
                      onClick={() => askInstall(row)}
                      disabled={working || row.in_progress}
                      data-testid={`firmware-install-${row.id}`}
                      className="glass-button min-h-11 px-3 text-[10px] font-black uppercase tracking-widest text-emerald-200 disabled:opacity-50"
                    >
                      {working ? 'Starting' : 'Install'}
                    </button>
                  )}
                  {row.action === 'upgrade' && (
                    <button
                      type="button"
                      onClick={() => askUpgrade(row)}
                      disabled={working}
                      data-testid={`firmware-upgrade-${row.id}`}
                      className="glass-button min-h-11 px-3 text-[10px] font-black uppercase tracking-widest disabled:opacity-50"
                    >
                      {working ? 'Asking' : 'Update firmware'}
                    </button>
                  )}
                  {row.release_url && (
                    <a
                      href={row.release_url}
                      target="_blank"
                      rel="noreferrer"
                      className="glass-button flex min-h-11 items-center px-3 text-[10px] font-black uppercase tracking-widest text-slate-300"
                    >
                      Notes
                    </a>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p className="text-xs text-slate-500">
        ESPHome boards update through the Device Builder, so their new version is compiled first and flashed over the
        air. A board whose builder is not linked says so instead of offering an update.
      </p>
    </section>
  );
}

export default FirmwarePanel;
