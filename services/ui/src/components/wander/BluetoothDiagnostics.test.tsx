import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => platform.name } };
});
const plugin = vi.hoisted(() => ({ getDiagnostics: vi.fn() }));
vi.mock('../../plugins/vehicleBluetooth', () => ({ default: plugin }));
const nearby = vi.hoisted(() => ({ getDiagnostics: vi.fn() }));
vi.mock('../../plugins/proximity', () => ({ default: nearby }));
const mesh = vi.hoisted(() => ({ getDiagnostics: vi.fn() }));
vi.mock('../../plugins/familyMesh', () => ({ default: mesh }));
const own = vi.hoisted(() => ({ getDiagnostics: vi.fn(), state: vi.fn() }));
vi.mock('../../plugins/ownSync', () => ({ default: own }));

import BluetoothDiagnostics from './BluetoothDiagnostics';

describe('BluetoothDiagnostics', () => {
  beforeEach(() => {
    platform.name = 'android';
    plugin.getDiagnostics.mockResolvedValue({
      supported: true, permissionGranted: true, bluetoothOn: true, apiLevel: 28,
      linkedConnected: [{ address: '00:1A:7D:DA:71:13', vehicle: 'Van' }], otherConnectedCount: 2, linkedCount: 3,
      linksRefreshedAt: null,
    });
    nearby.getDiagnostics.mockResolvedValue({
      enabled: true, scanPermission: true, advertisePermission: true, bluetoothOn: true, running: true,
      advertising: 'unsupported', scanning: true, scanMode: 'low_power', driving: false, lastScanStartAt: 1,
      lastHeardAt: 2, configured: true, keyId: 'abc123', members: 4, outbox: 0, uploadedAt: null,
      peers: [{ person: 'robin', band: 'same_room', rssi: -72, at: 2 }],
    });
    mesh.getDiagnostics.mockResolvedValue({
      enabled: true, connectPermission: true, rooms: 2, server: false, syncing: null, stored: 3, unsynced: 1,
      hasMail: true, lastSentAt: null, lastReceivedAt: null, lastServerSyncAt: null,
      lastPeerSync: { at: Date.UTC(2026, 10, 24, 18), peer: 'robin', sent: 1, received: 2 }, lastError: null,
    });
    own.getDiagnostics.mockResolvedValue({ enabled: false, turnedOn: false, peers: [] });
    own.state.mockResolvedValue({ enabled: false, devices: [] });
  });

  it("lists each of my other devices: when it last synced and, per kind, in sync or where it came from", async () => {
    const at = new Date(2026, 10, 25, 15, 42).getTime();
    own.getDiagnostics.mockResolvedValue({
      enabled: true, turnedOn: true, deviceId: 'phone-id', name: 'Test Phone', ownItems: 5,
      peers: [{
        device: 'tablet-id', name: 'Test Tablet', last_sync_at: at, first_seen_at: at,
        types: {
          device: { status: 'in_sync', at },
          steps: { status: 'updated', from: 'Test Tablet', at },
          family: { status: 'updated', from: 'Test Tablet', at },
          chat: { status: 'in_sync', at },
        },
      }],
    });
    own.state.mockResolvedValue({
      enabled: true, deviceId: 'phone-id', devices: [],
      steps: { day: '2026-11-25', total: 6200, devices: [
        { device: 'phone-id', name: 'Test Phone', me: true, steps: 4100, at },
        { device: 'tablet-id', name: 'Test Tablet', me: false, steps: 1200, at },
        { device: 'watch', name: 'Jarvis Watch', me: false, steps: 6200, at },
      ] },
      latestFix: { lat: 1, lon: 2, at, device: 'tablet-id', name: 'Test Tablet' },
    });
    render(<BluetoothDiagnostics />);
    const peer = await screen.findByTestId('diag-own-peer');
    expect(peer).toHaveTextContent('Test Tablet');
    expect(screen.getByTestId('diag-own-peer-synced')).toHaveTextContent('Nov 25');
    expect(screen.getByTestId('diag-own-steps')).toHaveTextContent('6,200 (this one 4,100, Test Tablet 1,200, Jarvis Watch 6,200)');
    expect(screen.getByTestId('diag-own-fix')).toHaveTextContent('From Test Tablet');
    expect(screen.getByTestId('diag-own-type-device')).toHaveTextContent('In sync');
    expect(screen.getByTestId('diag-own-type-steps')).toHaveTextContent(/Updated from Test Tablet at Nov 25, 3:42/);
    expect(screen.getByTestId('diag-own-type-family')).toHaveTextContent(/Updated from Test Tablet at/);
    // A kind the device has never reported on (an older build on the other side) says so.
    expect(screen.getByTestId('diag-own-type-watch')).toHaveTextContent('Not synced yet');
  });

  it('says when no other device of mine has synced yet, and leaves the section out on an older app', async () => {
    own.getDiagnostics.mockResolvedValue({ enabled: true, turnedOn: true, peers: [] });
    const { unmount } = render(<BluetoothDiagnostics />);
    expect(await screen.findByTestId('diag-own-none')).toHaveTextContent('None synced yet');
    unmount();
    own.getDiagnostics.mockRejectedValue(new Error('not implemented'));
    render(<BluetoothDiagnostics />);
    await screen.findByTestId('diag-mesh-peer');
    expect(screen.queryByTestId('diag-own')).toBeNull();
  });

  it('shows the offline chat: the last sync with a phone and what it moved', async () => {
    render(<BluetoothDiagnostics />);
    expect(await screen.findByTestId('diag-mesh-peer')).toHaveTextContent(/robin at .*: sent 1, got 2/);
    expect(screen.getByTestId('diag-mesh-sent')).toHaveTextContent('never');
  });

  it('shows a scan-only tablet, its scan and the phones it heard', async () => {
    render(<BluetoothDiagnostics />);
    expect(await screen.findByTestId('diag-advertiser')).toHaveTextContent('Not supported on this device (scan only)');
    expect(screen.getByTestId('diag-scan')).toHaveTextContent('Running (low power)');
    expect(screen.getByTestId('diag-peers')).toHaveTextContent('robin: In the same room (-72 dBm');
  });

  it('shows the linked car the next fix will carry, and the permission', async () => {
    render(<BluetoothDiagnostics />);
    expect(await screen.findByTestId('diag-bt-vehicle')).toHaveTextContent('Van (00:1A:7D:DA:71:13)');
    expect(screen.getByTestId('diag-bt-permission')).toHaveTextContent('Allowed');
    expect(screen.getByText('3, read never')).toBeInTheDocument();
  });

  it('is not drawn in the browser', () => {
    platform.name = 'web';
    const { container } = render(<BluetoothDiagnostics />);
    expect(container).toBeEmptyDOMElement();
  });
});
