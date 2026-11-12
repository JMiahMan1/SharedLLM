import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { BeaconStatus } from '../../plugins/bleBeacon';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => platform.name } };
});
const beacon = vi.hoisted(() => ({
  getStatus: vi.fn(),
  setEnabled: vi.fn(),
  setTxPower: vi.fn(),
  requestPermission: vi.fn(),
  openSettings: vi.fn(),
  addListener: vi.fn(),
}));
vi.mock('../../plugins/bleBeacon', () => ({ default: beacon }));

import RoomDetectionPanel, { beaconStateLine } from './RoomDetectionPanel';

const base: BeaconStatus = {
  enabled: true,
  state: 'advertising',
  reason: null,
  uuid: '00000000-0000-4000-8000-000000000001',
  major: 10,
  minor: 20,
  txPowerLevel: 'low',
  measuredPower: -75,
  permissionGranted: true,
  canDisable: true,
  hubRegistered: true,
  hubDetail: 'registered',
};

describe('RoomDetectionPanel', () => {
  beforeEach(() => {
    platform.name = 'android';
    vi.clearAllMocks();
    beacon.addListener.mockResolvedValue({ remove: vi.fn().mockResolvedValue(undefined) });
    beacon.openSettings.mockResolvedValue(undefined);
  });

  it('is not shown off Android', () => {
    platform.name = 'web';
    const { container } = render(<RoomDetectionPanel />);
    expect(container).toBeEmptyDOMElement();
    expect(beacon.getStatus).not.toHaveBeenCalled();
  });

  it('shows the beacon on and lets an adult switch it off', async () => {
    beacon.getStatus.mockResolvedValue(base);
    beacon.setEnabled.mockResolvedValue({ ...base, enabled: false, state: 'disabled' });
    render(<RoomDetectionPanel />);
    await waitFor(() => expect(screen.getByTestId('room-detection-state')).toHaveTextContent('On: the house can tell'));
    const toggle = screen.getByTestId('room-detection-toggle');
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    await userEvent.click(toggle);
    expect(beacon.setEnabled).toHaveBeenCalledWith({ enabled: false });
    await waitFor(() => expect(screen.getByTestId('room-detection-state')).toHaveTextContent('Off:'));
  });

  it('says plainly when room detection is unavailable and offers the permission', async () => {
    beacon.getStatus.mockResolvedValue({
      ...base,
      state: 'unavailable',
      permissionGranted: false,
      reason: 'Bluetooth permission (Nearby devices) not granted to Jarvis',
    });
    beacon.requestPermission.mockResolvedValue(base);
    render(<RoomDetectionPanel />);
    await waitFor(() =>
      expect(screen.getByTestId('room-detection-state')).toHaveTextContent(
        'Room detection unavailable: Bluetooth permission (Nearby devices) not granted to Jarvis',
      ),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Open settings' }));
    expect(beacon.openSettings).toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Allow Nearby devices' }));
    expect(beacon.requestPermission).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId('room-detection-state')).toHaveTextContent('On:'));
  });

  it('cannot be switched off on a child phone', async () => {
    beacon.getStatus.mockResolvedValue({ ...base, canDisable: false });
    render(<RoomDetectionPanel />);
    await waitFor(() => expect(screen.getByTestId('room-detection-locked')).toBeInTheDocument());
    expect(screen.getByTestId('room-detection-toggle')).toBeDisabled();
  });

  it('changes the signal strength', async () => {
    beacon.getStatus.mockResolvedValue(base);
    beacon.setTxPower.mockResolvedValue({ ...base, txPowerLevel: 'high', measuredPower: -59 });
    render(<RoomDetectionPanel />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Low' })).toHaveAttribute('aria-pressed', 'true'));
    await userEvent.click(screen.getByRole('button', { name: 'High' }));
    expect(beacon.setTxPower).toHaveBeenCalledWith({ level: 'high' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'High' })).toHaveAttribute('aria-pressed', 'true'));
  });

  it('shows a hub that has not registered the beacon, and a native failure', async () => {
    beacon.getStatus.mockResolvedValue({ ...base, hubRegistered: false, hubDetail: 'no phone on the hub for this user' });
    beacon.setEnabled.mockRejectedValue(new Error('Room detection stays on on a child phone'));
    render(<RoomDetectionPanel />);
    await waitFor(() => expect(screen.getByTestId('room-detection-hub')).toHaveTextContent('no phone on the hub for this user'));
    await userEvent.click(screen.getByTestId('room-detection-toggle'));
    await waitFor(() => expect(screen.getByTestId('room-detection-error')).toHaveTextContent('stays on'));
  });

  it('a status that cannot be read is said, not hidden', async () => {
    beacon.getStatus.mockRejectedValue(new Error('plugin is not implemented on android'));
    render(<RoomDetectionPanel />);
    await waitFor(() =>
      expect(screen.getByTestId('room-detection-error')).toHaveTextContent('Room detection unavailable: plugin is not implemented'),
    );
  });

  it('words every state', () => {
    expect(beaconStateLine({ ...base, state: 'bluetooth_off' })).toContain('Waiting for Bluetooth');
    expect(beaconStateLine({ ...base, state: 'starting' })).toContain('Starting');
    expect(beaconStateLine({ ...base, state: 'unavailable', reason: null })).toBe(
      'Room detection unavailable: the phone did not say why',
    );
  });
});
