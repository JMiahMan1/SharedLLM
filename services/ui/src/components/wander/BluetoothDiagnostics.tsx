import { useCallback, useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { Stethoscope, RefreshCw } from 'lucide-react';
import VehicleBluetooth, { type VehicleBtDiagnostics } from '../../plugins/vehicleBluetooth';

/** One line of the panel: what is checked, and what this phone says. */
export function DiagRow({ label, value, ok, testId }: { label: string; value: string; ok?: boolean | null; testId?: string }) {
  const tone = ok === true ? 'text-emerald-300' : ok === false ? 'text-amber-300' : 'text-slate-200';
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1" data-testid={testId}>
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className={`min-w-0 break-words text-xs font-medium ${tone}`}>{value}</dd>
    </div>
  );
}

function when(ms: number | null | undefined): string {
  if (!ms) return 'never';
  return new Date(ms).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/**
 * Wander's Bluetooth diagnostics, for a parent checking a real phone (the
 * Android app only): enough to verify each step of the manual checklist
 * without a cable. Vehicle detection: the Nearby devices permission,
 * Bluetooth, the linked car connected now (what the next fix carries).
 */
export default function BluetoothDiagnostics() {
  const android = Capacitor.getPlatform() === 'android';
  const [vehicle, setVehicle] = useState<VehicleBtDiagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    VehicleBluetooth.getDiagnostics()
      .then((v) => {
        setVehicle(v);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Could not read this phone'));
  }, []);

  useEffect(() => {
    if (android) load();
  }, [android, load]);

  if (!android) return null;

  return (
    <details className="glass-panel rounded-2xl border border-white/5 p-4" data-testid="bt-diagnostics">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 text-sm font-semibold text-slate-200">
        <Stethoscope size={15} className="text-sky-300" /> Bluetooth diagnostics (this phone)
      </summary>
      <div className="mt-2 space-y-3">
        {error && <p className="text-xs text-amber-300">{error}</p>}
        {vehicle && (
          <section>
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Vehicle detection</h4>
            <dl className="divide-y divide-white/5">
              <DiagRow label="Android API" value={String(vehicle.apiLevel)} />
              <DiagRow label="Nearby devices (connect)" value={vehicle.permissionGranted ? 'Allowed' : 'Not allowed'} ok={vehicle.permissionGranted} testId="diag-bt-permission" />
              <DiagRow label="Bluetooth" value={vehicle.supported ? (vehicle.bluetoothOn ? 'On' : 'Off') : 'Not on this device'} ok={vehicle.supported && vehicle.bluetoothOn} />
              <DiagRow
                label="Linked car connected now"
                value={vehicle.linkedConnected.length
                  ? vehicle.linkedConnected.map((v) => `${v.vehicle ?? 'Vehicle'} (${v.address})`).join(', ')
                  : 'None'}
                ok={vehicle.linkedConnected.length > 0 ? true : null}
                testId="diag-bt-vehicle"
              />
              <DiagRow label="Other devices connected" value={String(vehicle.otherConnectedCount)} />
              <DiagRow label="Household links known" value={`${vehicle.linkedCount}, read ${when(vehicle.linksRefreshedAt)}`} />
            </dl>
          </section>
        )}
        <button type="button" onClick={load} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold text-purple-300 hover:bg-white/10">
          <RefreshCw size={13} /> Refresh
        </button>
      </div>
    </details>
  );
}
