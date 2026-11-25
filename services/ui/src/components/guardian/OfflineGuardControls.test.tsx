import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => platform.name } };
});
const offlinePlugin = vi.hoisted(() => ({ sendCommand: vi.fn(), commands: vi.fn() }));
vi.mock('../../plugins/offlineMode', () => ({ default: offlinePlugin }));
const nearbyPlugin = vi.hoisted(() => ({ getState: vi.fn() }));
vi.mock('../../plugins/nearby', () => ({ default: nearbyPlugin }));

import OfflineGuardControls from './OfflineGuardControls';

const at = new Date(2026, 10, 24, 15, 41).getTime();
const draw = () => render(<MemoryRouter><OfflineGuardControls username="robin" name="Robin" /></MemoryRouter>);

describe('OfflineGuardControls', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    platform.name = 'android';
    nearbyPlugin.getState.mockResolvedValue({ offline: true });
    offlinePlugin.commands.mockResolvedValue({ commands: [
      { id: 'c', child: 'robin', action: 'lock', arg: 0, issued_at: at / 1000, reported: false },
    ] });
    offlinePlugin.sendCommand.mockResolvedValue({ id: 'y' });
  });

  it('locks, unlocks and gives time over Bluetooth, with where the last one got to', async () => {
    draw();
    expect(await screen.findByTestId('offline-delivery')).toHaveTextContent('Locked sent at 3:41 PM. Not reached yet');
    await userEvent.click(screen.getByRole('button', { name: /Lock/ }));
    expect(offlinePlugin.sendCommand).toHaveBeenCalledWith({ child: 'robin', action: 'lock', minutes: 0 });
    await userEvent.click(screen.getByRole('button', { name: /15 more minutes/ }));
    expect(offlinePlugin.sendCommand).toHaveBeenCalledWith({ child: 'robin', action: 'bonus', minutes: 15 });
  });

  it('points to Settings when offline mode is off on this phone', async () => {
    nearbyPlugin.getState.mockResolvedValue({ offline: false });
    draw();
    const line = await screen.findByTestId('guardian-offline-off');
    expect(line).toHaveTextContent("to reach Robin's phone");
    expect(screen.getByRole('link', { name: /Nearby & offline/ })).toHaveAttribute('href', '/settings#nearby-offline');
    expect(screen.queryByRole('button', { name: /Lock/ })).not.toBeInTheDocument();
  });

  it('is not drawn in the browser', () => {
    platform.name = 'web';
    const { container } = draw();
    expect(container).toBeEmptyDOMElement();
  });
});
