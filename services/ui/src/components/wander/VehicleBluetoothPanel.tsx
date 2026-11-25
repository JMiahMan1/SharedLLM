import { useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { Bluetooth, BluetoothOff, Car, Link2, Trash2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { errorText } from '../../lib/errorText';
import { confirmDialog } from '../ui/confirmDialog';
import VehicleBluetooth, { type VehicleBtStatus } from '../../plugins/vehicleBluetooth';
import {
  deviceRows,
  linkLabel,
  shortAddress,
  type DeviceRow,
  type VehicleBtLink,
  type VehicleWithLinks,
} from '../../lib/vehicleBluetooth';

interface Props {
  vehicles: VehicleWithLinks[];
  /** The signed-in person (their own links can be removed). */
  currentUser: string;
  /** A parent may remove any link. */
  isAdmin: boolean;
  /** After a link changed, so the caller reloads the vehicles. */
  onChanged: () => void;
}

/**
 * Each household vehicle with the Bluetooth devices it is linked to. On the
 * Android app, "Link a device" lists the phone's paired devices; picking one
 * links it by its hardware address, so a renamed head unit is still the same
 * car. A trip then takes the vehicle whose device was connected.
 */
export default function VehicleBluetoothPanel({ vehicles, currentUser, isAdmin, onChanged }: Props) {
  const android = Capacitor.getPlatform() === 'android';
  const [openFor, setOpenFor] = useState<string | null>(null);
  const [status, setStatus] = useState<VehicleBtStatus | null>(null);
  const [rows, setRows] = useState<DeviceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  // The phone keeps the linked addresses to tag its fixes; hand it the latest.
  useEffect(() => {
    if (android) void VehicleBluetooth.setVehicles({ vehicles }).catch(() => undefined);
  }, [android, vehicles]);

  if (vehicles.length === 0) return null;

  const vehicle = vehicles.find((v) => v.id === openFor) ?? null;

  const loadDevices = async (target: VehicleWithLinks) => {
    setLoading(true);
    try {
      let s = await VehicleBluetooth.getStatus();
      if (!s.permissionGranted) s = await VehicleBluetooth.requestPermission();
      setStatus(s);
      if (!s.permissionGranted || !s.bluetoothOn) {
        setRows([]);
        return;
      }
      const answer = await VehicleBluetooth.listDevices();
      setRows(deviceRows(answer.devices, target, vehicles));
    } catch (e) {
      toast.error(errorText(e, 'Could not read the paired devices'));
    } finally {
      setLoading(false);
    }
  };

  const open = (v: VehicleWithLinks) => {
    if (openFor === v.id) {
      setOpenFor(null);
      return;
    }
    setOpenFor(v.id);
    setRows([]);
    void loadDevices(v);
  };

  const link = async (v: VehicleWithLinks, row: DeviceRow) => {
    setBusy(row.address);
    try {
      await api.linkVehicleBluetooth(v.id, { address: row.address, kind: row.kind, name: row.name });
      toast.success(`${row.name || 'Device'} linked to ${v.name}`);
      setOpenFor(null);
      onChanged();
    } catch (e) {
      toast.error(errorText(e, 'Could not link that device'));
    } finally {
      setBusy(null);
    }
  };

  const unlink = async (v: VehicleWithLinks, l: VehicleBtLink) => {
    const ok = await confirmDialog({
      title: `Unlink ${linkLabel(l)}?`,
      body: `Trips will no longer pick ${v.name} when this device is connected.`,
      confirmLabel: 'Unlink',
    });
    if (!ok) return;
    setBusy(l.address);
    try {
      await api.unlinkVehicleBluetooth(v.id, l.address);
      toast.success('Unlinked');
      onChanged();
    } catch (e) {
      toast.error(errorText(e, 'Could not unlink that device'));
    } finally {
      setBusy(null);
    }
  };

  const mayUnlink = (l: VehicleBtLink) => isAdmin || (l.linked_by ?? '').toLowerCase() === currentUser.toLowerCase();

  return (
    <div id="wander-vehicles" className="glass-panel scroll-mt-4 space-y-3 rounded-2xl border border-white/5 p-4" data-testid="vehicle-bt-panel">
      <div className="space-y-1">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <Car size={15} className="text-amber-400" /> Vehicles
        </h3>
        <p className="text-xs text-slate-400">
          Link a car's Bluetooth and trips pick that car by themselves whenever a phone is connected to it.
          {!android && ' Link one from the Jarvis app on a phone that is paired with the car.'}
        </p>
      </div>

      <ul className="space-y-2">
        {vehicles.map((v) => {
          const links = v.bluetooth ?? [];
          return (
            <li key={v.id} className="space-y-2 rounded-xl border border-white/5 bg-white/5 p-3" data-testid={`vehicle-bt-${v.id}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 truncate text-sm font-semibold text-white">{v.name}</span>
                {android && (
                  <button
                    type="button"
                    onClick={() => open(v)}
                    aria-expanded={openFor === v.id}
                    className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold text-purple-300 hover:bg-white/10"
                  >
                    {openFor === v.id ? <X size={14} /> : <Link2 size={14} />}
                    {openFor === v.id ? 'Close' : 'Link a device'}
                  </button>
                )}
              </div>

              {links.length === 0 ? (
                <p className="text-xs text-slate-500">No Bluetooth linked yet.</p>
              ) : (
                <ul className="flex flex-wrap gap-2" aria-label={`Devices linked to ${v.name}`}>
                  {links.map((l) => (
                    <li
                      key={l.address}
                      className="flex min-w-0 max-w-full items-center gap-1.5 rounded-full border border-sky-500/30 bg-sky-500/10 py-1 pl-3 pr-1 text-xs text-sky-100"
                    >
                      <Bluetooth size={12} className="shrink-0" />
                      <span className="min-w-0 truncate">{linkLabel(l)}</span>
                      <span className="shrink-0 font-mono text-[10px] text-sky-300/70">{shortAddress(l.address)}</span>
                      {mayUnlink(l) && (
                        <button
                          type="button"
                          onClick={() => void unlink(v, l)}
                          disabled={busy === l.address}
                          aria-label={`Unlink ${linkLabel(l)} from ${v.name}`}
                          className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-full text-sky-200 hover:bg-white/10 hover:text-red-300 disabled:opacity-50 sm:min-h-8 sm:min-w-8"
                        >
                          <Trash2 size={12} />
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}

              {vehicle?.id === v.id && (
                <DevicePicker
                  vehicle={v}
                  status={status}
                  rows={rows}
                  loading={loading}
                  busy={busy}
                  onPick={(row) => void link(v, row)}
                  onRetry={() => void loadDevices(v)}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function DevicePicker({
  vehicle,
  status,
  rows,
  loading,
  busy,
  onPick,
  onRetry,
}: {
  vehicle: VehicleWithLinks;
  status: VehicleBtStatus | null;
  rows: DeviceRow[];
  loading: boolean;
  busy: string | null;
  onPick: (row: DeviceRow) => void;
  onRetry: () => void;
}) {
  if (loading) return <p className="text-xs text-slate-400">Reading the phone's paired devices...</p>;
  if (status && !status.permissionGranted) {
    return (
      <div className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-100" data-testid="vehicle-bt-permission">
        <p>Jarvis needs Nearby devices to read a device's hardware address. Allow it, then try again.</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void VehicleBluetooth.openSettings()} className="min-h-11 rounded-lg px-3 font-semibold text-amber-200 underline">
            Open app settings
          </button>
          <button type="button" onClick={onRetry} className="min-h-11 rounded-lg px-3 font-semibold text-amber-200 underline">
            Try again
          </button>
        </div>
      </div>
    );
  }
  if (status && !status.bluetoothOn) {
    return (
      <p className="flex items-center gap-2 text-xs text-amber-200">
        <BluetoothOff size={14} /> Bluetooth is off on this phone. Turn it on to see its paired devices.
      </p>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="space-y-2 text-xs text-slate-400">
        <p>This phone has no paired devices. Pair it with the car first, in Android's Bluetooth settings.</p>
        <button type="button" onClick={() => void VehicleBluetooth.openBluetoothSettings()} className="min-h-11 rounded-lg font-semibold text-purple-300 underline">
          Open Bluetooth settings
        </button>
      </div>
    );
  }
  return (
    <ul className="space-y-1.5" aria-label={`Paired devices to link to ${vehicle.name}`} data-testid="vehicle-bt-devices">
      {rows.map((row) => (
        <li key={row.address}>
          <button
            type="button"
            disabled={!row.pickable || busy !== null}
            onClick={() => onPick(row)}
            className="flex min-h-11 w-full items-start gap-2 rounded-lg border border-white/5 px-3 py-2 text-left hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Bluetooth size={14} className={`mt-0.5 shrink-0 ${row.connected ? 'text-sky-300' : 'text-slate-500'}`} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm text-white">{row.name || 'Unnamed device'}</span>
              <span className="block text-[11px] text-slate-400">
                <span className="font-mono">{row.address}</span>
              </span>
              <span className={`block text-[11px] ${row.pickable ? 'text-emerald-300' : 'text-slate-400'}`}>{row.note}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
