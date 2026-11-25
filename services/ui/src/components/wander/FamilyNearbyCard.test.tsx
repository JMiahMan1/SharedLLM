import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ProximityDiagnostics } from '../../plugins/proximity';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => platform.name } };
});
const plugin = vi.hoisted(() => ({ getDiagnostics: vi.fn(), enable: vi.fn(), disable: vi.fn() }));
vi.mock('../../plugins/proximity', () => ({ default: plugin }));

import FamilyNearbyCard from './FamilyNearbyCard';
import { nearbyStatusLine } from '../../lib/proximity';

const base: ProximityDiagnostics = {
  enabled: false, scanPermission: false, advertisePermission: false, bluetoothOn: true, running: false,
  advertising: 'off', scanning: false, scanMode: 'low_power', driving: false, lastScanStartAt: null,
  lastHeardAt: null, configured: false, keyId: null, members: 0, outbox: 0, uploadedAt: null, peers: [],
};

describe('FamilyNearbyCard', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    platform.name = 'android';
  });

  it('asks for nothing until it is turned on', async () => {
    plugin.getDiagnostics.mockResolvedValue(base);
    plugin.enable.mockResolvedValue({ ...base, enabled: true, scanPermission: true, configured: true, running: true, advertising: 'on' });
    render(<FamilyNearbyCard />);
    expect(await screen.findByTestId('family-nearby-status')).toHaveTextContent('Off on this device.');
    expect(plugin.enable).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Turn on' }));
    expect(plugin.enable).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('family-nearby-status')).toHaveTextContent(/without Wi-Fi or data/);
    expect(screen.getByRole('button', { name: 'Turn off' })).toBeInTheDocument();
  });

  it('says plainly when a tablet can only listen', () => {
    expect(nearbyStatusLine({ ...base, enabled: true, scanPermission: true, configured: true, advertising: 'unsupported' }))
      .toMatch(/listening only/);
    expect(nearbyStatusLine({ ...base, enabled: true })).toMatch(/Nearby devices/);
  });

  it('is not drawn in the browser', () => {
    platform.name = 'web';
    const { container } = render(<FamilyNearbyCard />);
    expect(container).toBeEmptyDOMElement();
  });
});
