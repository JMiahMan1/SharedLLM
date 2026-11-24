import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { BondedDevice } from '../../plugins/vehicleBluetooth';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => platform.name } };
});
const plugin = vi.hoisted(() => ({
  getStatus: vi.fn(),
  requestPermission: vi.fn(),
  listDevices: vi.fn(),
  setVehicles: vi.fn(),
  openSettings: vi.fn(),
  openBluetoothSettings: vi.fn(),
}));
vi.mock('../../plugins/vehicleBluetooth', () => ({ default: plugin }));
const api = vi.hoisted(() => ({ linkVehicleBluetooth: vi.fn(), unlinkVehicleBluetooth: vi.fn() }));
vi.mock('../../services/api', () => ({ api }));
const confirm = vi.hoisted(() => vi.fn());
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: confirm }));

import VehicleBluetoothPanel from './VehicleBluetoothPanel';

const HEAD_UNIT = '00:1A:7D:DA:71:13';
const vehicles = [
  { id: 'van', name: 'Van', bluetooth: [{ address: HEAD_UNIT, name: 'Car Audio', linked_by: 'pat' }] },
  { id: 'truck', name: 'Truck' },
];
const ok = { supported: true, permissionGranted: true, bluetoothOn: true };
const device = (over: Partial<BondedDevice>): BondedDevice => ({
  name: 'Truck Radio', address: '00:00:00:00:00:09', type: 'classic', connected: true,
  linkable: true, kind: 'classic', reason: null, linkedTo: null, ...over,
});

describe('VehicleBluetoothPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    platform.name = 'android';
    plugin.setVehicles.mockResolvedValue(undefined);
    plugin.getStatus.mockResolvedValue(ok);
    plugin.requestPermission.mockResolvedValue(ok);
    confirm.mockResolvedValue(true);
  });

  it('shows each vehicle with its linked devices and hands the phone the links', async () => {
    render(<VehicleBluetoothPanel vehicles={vehicles} currentUser="pat" isAdmin={false} onChanged={vi.fn()} />);
    const van = screen.getByTestId('vehicle-bt-van');
    expect(within(van).getByText('Car Audio')).toBeInTheDocument();
    expect(within(van).getByText('...DA:71:13')).toBeInTheDocument();
    expect(within(screen.getByTestId('vehicle-bt-truck')).getByText(/No Bluetooth linked yet/)).toBeInTheDocument();
    await waitFor(() => expect(plugin.setVehicles).toHaveBeenCalledWith({ vehicles }));
  });

  it('links a paired device by its address, not its name', async () => {
    const onChanged = vi.fn();
    plugin.listDevices.mockResolvedValue({ ...ok, devices: [device({}), device({ name: 'Watch', address: '4A:11:22:33:44:55', linkable: false, kind: null, reason: 'Pair it first' })] });
    api.linkVehicleBluetooth.mockResolvedValue({ status: 'ok', vehicle: {} });
    render(<VehicleBluetoothPanel vehicles={vehicles} currentUser="pat" isAdmin={false} onChanged={onChanged} />);
    await userEvent.click(within(screen.getByTestId('vehicle-bt-truck')).getByRole('button', { name: /Link a device/ }));
    const list = await screen.findByTestId('vehicle-bt-devices');
    expect(within(list).getByRole('button', { name: /Watch/ })).toBeDisabled();
    expect(within(list).getByText('Pair it first')).toBeInTheDocument();
    await userEvent.click(within(list).getByRole('button', { name: /Truck Radio/ }));
    expect(api.linkVehicleBluetooth).toHaveBeenCalledWith('truck', { address: '00:00:00:00:00:09', kind: 'classic', name: 'Truck Radio' });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('asks for Nearby devices and explains a refusal', async () => {
    plugin.getStatus.mockResolvedValue({ ...ok, permissionGranted: false });
    plugin.requestPermission.mockResolvedValue({ ...ok, permissionGranted: false });
    render(<VehicleBluetoothPanel vehicles={vehicles} currentUser="pat" isAdmin={false} onChanged={vi.fn()} />);
    await userEvent.click(within(screen.getByTestId('vehicle-bt-truck')).getByRole('button', { name: /Link a device/ }));
    expect(await screen.findByTestId('vehicle-bt-permission')).toHaveTextContent(/Nearby devices/);
    expect(plugin.requestPermission).toHaveBeenCalled();
    expect(plugin.listDevices).not.toHaveBeenCalled();
  });

  it('lets the person who linked a device, or a parent, unlink it', async () => {
    const { rerender } = render(<VehicleBluetoothPanel vehicles={vehicles} currentUser="alex" isAdmin={false} onChanged={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /Unlink Car Audio/ })).toBeNull();
    rerender(<VehicleBluetoothPanel vehicles={vehicles} currentUser="pat" isAdmin={false} onChanged={vi.fn()} />);
    api.unlinkVehicleBluetooth.mockResolvedValue({ status: 'ok', vehicle: {} });
    await userEvent.click(screen.getByRole('button', { name: /Unlink Car Audio from Van/ }));
    expect(api.unlinkVehicleBluetooth).toHaveBeenCalledWith('van', HEAD_UNIT);
  });

  it('on the web says to link from the phone, with no link button', () => {
    platform.name = 'web';
    render(<VehicleBluetoothPanel vehicles={vehicles} currentUser="pat" isAdmin onChanged={vi.fn()} />);
    expect(screen.getByTestId('vehicle-bt-panel')).toHaveTextContent(/from the Jarvis app on a phone/);
    expect(screen.queryByRole('button', { name: /Link a device/ })).toBeNull();
  });
});
