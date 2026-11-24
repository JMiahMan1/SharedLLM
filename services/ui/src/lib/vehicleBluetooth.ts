/**
 * Wander's vehicles told apart by Bluetooth hardware (geo's vehicle_bluetooth):
 * what a link looks like, how a trip says where its vehicle came from, and
 * how the phone's paired devices are listed for linking.
 *
 * The key is always the hardware address. A device's name is shown only; it
 * can be renamed at any time and the link still holds.
 */
import type { BondedDevice } from '../plugins/vehicleBluetooth';

export interface VehicleBtLink {
  address: string;
  kind?: 'classic' | 'le_public' | 'le_static' | string;
  /** The device's name when it was linked (display only). */
  name?: string;
  linked_by?: string;
  linked_at?: number;
}

export interface VehicleWithLinks {
  id: string;
  name: string;
  bluetooth?: VehicleBtLink[];
}

export type VehicleSource = 'default' | 'bluetooth' | 'manual';

/** `aa-bb-...` / `AABB...` to `AA:BB:CC:DD:EE:FF`, or null. */
export function normaliseAddress(value: string | null | undefined): string | null {
  const raw = String(value ?? '').replace(/[\s:.-]/g, '').toUpperCase();
  if (!/^[0-9A-F]{12}$/.test(raw)) return null;
  if (raw === '020000000000' || raw === '000000000000' || raw === 'FFFFFFFFFFFF') return null;
  return raw.match(/.{2}/g)!.join(':');
}

/** The last three bytes, enough to tell two devices apart on screen without filling it. */
export function shortAddress(address: string): string {
  const a = normaliseAddress(address);
  return a ? `...${a.slice(9)}` : address;
}

/** The vehicle an address is linked to, if any. */
export function vehicleOfAddress<V extends VehicleWithLinks>(vehicles: V[], address: string): V | undefined {
  const a = normaliseAddress(address);
  if (!a) return undefined;
  return vehicles.find((v) => (v.bluetooth ?? []).some((l) => normaliseAddress(l.address) === a));
}

/** A link's label: its name, or the address when it has none. */
export function linkLabel(link: VehicleBtLink): string {
  const name = (link.name ?? '').trim();
  return name || `Device ${shortAddress(link.address)}`;
}

/** How a trip says where its vehicle came from, or null when nothing worth saying. */
export function vehicleSourceLabel(source: string | null | undefined): string | null {
  if (source === 'bluetooth') return 'Detected by Bluetooth';
  if (source === 'manual') return 'Chosen by hand';
  return null;
}

export interface DeviceRow extends BondedDevice {
  /** This device is linked to the vehicle being edited. */
  linkedHere: boolean;
  /** It is linked to a different vehicle (its name). */
  linkedElsewhere: string | null;
  /** It can be picked for this vehicle now. */
  pickable: boolean;
  /** What the row says under the name. */
  note: string;
}

/**
 * The phone's paired devices for linking to `vehicle`: connected ones first
 * (the car you are sitting in), then linkable ones, then the rest with their
 * reason. A device linked elsewhere says to which vehicle.
 */
export function deviceRows(devices: BondedDevice[], vehicle: VehicleWithLinks, vehicles: VehicleWithLinks[]): DeviceRow[] {
  const rows = devices.map((d): DeviceRow => {
    const owner = vehicleOfAddress(vehicles, d.address);
    const linkedHere = owner?.id === vehicle.id;
    const linkedElsewhere = owner && !linkedHere ? owner.name : null;
    const pickable = d.linkable && !linkedHere && !linkedElsewhere;
    let note: string;
    if (linkedHere) note = 'Linked to this vehicle';
    else if (linkedElsewhere) note = `Linked to ${linkedElsewhere}`;
    else if (!d.linkable) note = d.reason || 'This device cannot be linked';
    else note = d.connected ? 'Connected now' : 'Paired';
    return { ...d, linkedHere, linkedElsewhere, pickable, note };
  });
  const rank = (r: DeviceRow) => (r.connected ? 0 : 2) + (r.pickable ? 0 : 1);
  return rows.sort((a, b) => rank(a) - rank(b) || (a.name || a.address).localeCompare(b.name || b.address));
}
