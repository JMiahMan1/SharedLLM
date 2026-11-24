import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => platform.name } };
});
const plugin = vi.hoisted(() => ({ getDiagnostics: vi.fn() }));
vi.mock('../../plugins/vehicleBluetooth', () => ({ default: plugin }));

import BluetoothDiagnostics from './BluetoothDiagnostics';

describe('BluetoothDiagnostics', () => {
  beforeEach(() => {
    platform.name = 'android';
    plugin.getDiagnostics.mockResolvedValue({
      supported: true, permissionGranted: true, bluetoothOn: true, apiLevel: 28,
      linkedConnected: [{ address: '00:1A:7D:DA:71:13', vehicle: 'Van' }], otherConnectedCount: 2, linkedCount: 3,
      linksRefreshedAt: null,
    });
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
