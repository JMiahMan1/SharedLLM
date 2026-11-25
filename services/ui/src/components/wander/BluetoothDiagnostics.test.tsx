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
      peers: [{ person: 'gracie', band: 'same_room', rssi: -72, at: 2 }],
    });
    mesh.getDiagnostics.mockResolvedValue({
      enabled: true, connectPermission: true, room: true, server: false, syncing: null, stored: 3, unsynced: 1,
      hasMail: true, lastSentAt: null, lastReceivedAt: null, lastServerSyncAt: null,
      lastPeerSync: { at: Date.UTC(2026, 10, 24, 18), peer: 'gracie', sent: 1, received: 2 }, lastError: null,
    });
  });

  it('shows the offline chat: the last sync with a phone and what it moved', async () => {
    render(<BluetoothDiagnostics />);
    expect(await screen.findByTestId('diag-mesh-peer')).toHaveTextContent(/gracie at .*: sent 1, got 2/);
    expect(screen.getByTestId('diag-mesh-sent')).toHaveTextContent('never');
  });

  it('shows a scan-only tablet, its scan and the phones it heard', async () => {
    render(<BluetoothDiagnostics />);
    expect(await screen.findByTestId('diag-advertiser')).toHaveTextContent('Not supported on this device (scan only)');
    expect(screen.getByTestId('diag-scan')).toHaveTextContent('Running (low power)');
    expect(screen.getByTestId('diag-peers')).toHaveTextContent('gracie: In the same room (-72 dBm');
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
