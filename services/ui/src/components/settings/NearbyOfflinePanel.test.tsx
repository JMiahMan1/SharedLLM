import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { NearbyDeviceState } from '../../plugins/nearby';
import type { NearbySettingsState } from '../../lib/nearbySettings';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => platform.name } };
});
const nearby = vi.hoisted(() => ({ getState: vi.fn(), apply: vi.fn(), requestPermissions: vi.fn() }));
vi.mock('../../plugins/nearby', () => ({ default: nearby }));
const api = vi.hoisted(() => ({ getNearbySettings: vi.fn(), setNearbySettings: vi.fn() }));
vi.mock('../../services/api', () => ({ api }));
vi.mock('../wander/OfflineModePanel', () => ({ default: () => <div data-testid="offline-mode-stub" /> }));

import NearbyOfflinePanel from './NearbyOfflinePanel';

const device: NearbyDeviceState = {
  nearby: false, chat: false, offline: false, car: true, mine: false, parentLink: false,
  scanPermission: true, advertisePermission: true, connectPermission: true, bluetoothOn: true,
  host: 'none', apiLevel: 36,
};
const adult: NearbySettingsState = {
  username: 'casey', settings: { nearby: false, chat: false, offline: false, car: true, mine: false },
  managed_by_parent: false, chosen: true, can_change: true, always_on: [],
};
const child: NearbySettingsState = {
  username: 'robin', settings: { nearby: true, chat: false, offline: true, car: true, mine: false },
  managed_by_parent: true, chosen: true, can_change: false, always_on: ['parent_link'],
};

const draw = (path = '/settings') =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <NearbyOfflinePanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('NearbyOfflinePanel', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    platform.name = 'android';
    nearby.getState.mockResolvedValue(device);
    api.getNearbySettings.mockResolvedValue(adult);
  });

  it('shows one switch per part with a plain line each', async () => {
    draw();
    for (const name of ['Find family nearby', 'Offline family chat', 'Offline mode', 'Car detection']) {
      expect(await screen.findByRole('switch', { name })).toBeEnabled();
    }
    expect(screen.getByRole('switch', { name: 'Car detection' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('nearby-row-offline')).toHaveTextContent(/Voice Assistant/);
    expect(screen.getByTestId('nearby-offline-panel')).not.toHaveTextContent('Jarvis');
  });

  it('a grown member turns a part on: saved, applied on the phone, which may ask then', async () => {
    api.setNearbySettings.mockResolvedValue({ ...adult, settings: { ...adult.settings, chat: true } });
    nearby.apply.mockResolvedValue({ ...device, chat: true, host: 'own' });
    draw();
    await userEvent.click(await screen.findByRole('switch', { name: 'Offline family chat' }));
    expect(api.setNearbySettings).toHaveBeenCalledWith({ chat: true });
    expect(nearby.apply).toHaveBeenCalledWith({ nearby: false, chat: true, offline: false, car: true, mine: false, ask: true });
    expect(await screen.findByTestId('nearby-device-line')).toHaveTextContent('location sharing is off');
  });

  it('turning a part off never asks for a permission', async () => {
    api.getNearbySettings.mockResolvedValue({ ...adult, settings: { ...adult.settings, nearby: true } });
    api.setNearbySettings.mockResolvedValue(adult);
    nearby.apply.mockResolvedValue(device);
    draw();
    await userEvent.click(await screen.findByRole('switch', { name: 'Find family nearby' }));
    expect(nearby.apply).toHaveBeenCalledWith(expect.objectContaining({ nearby: false, ask: false }));
  });

  it("a child sees the switches read-only, says a parent manages them, and the parent link stays on", async () => {
    api.getNearbySettings.mockResolvedValue(child);
    draw();
    const s = await screen.findByRole('switch', { name: 'Find family nearby' });
    await waitFor(() => expect(s).toBeDisabled());
    expect(s).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('nearby-managed')).toHaveTextContent('A parent manages these');
    expect(screen.getByTestId('nearby-parent-link')).toHaveTextContent(/lock, unlock or give more time/);
    await userEvent.click(s);
    expect(api.setNearbySettings).not.toHaveBeenCalled();
  });

  it('offers Allow Nearby devices when a part is on and Android has not allowed it', async () => {
    api.getNearbySettings.mockResolvedValue({ ...adult, settings: { ...adult.settings, nearby: true } });
    nearby.getState.mockResolvedValue({ ...device, nearby: true, scanPermission: false });
    nearby.requestPermissions.mockResolvedValue({ ...device, nearby: true });
    draw();
    const line = await screen.findByTestId('nearby-device-line');
    await userEvent.click(within(line).getByRole('button', { name: 'Allow Nearby devices' }));
    expect(nearby.requestPermissions).toHaveBeenCalled();
  });

  it("opens offline mode's screen from the header line's link", async () => {
    api.getNearbySettings.mockResolvedValue({ ...adult, settings: { ...adult.settings, offline: true } });
    draw('/settings#offline');
    const details = await screen.findByTestId('nearby-offline-mode');
    await waitFor(() => expect(details).toHaveAttribute('open'));
    expect(screen.getByTestId('offline-mode-stub')).toBeInTheDocument();
  });

  it('in the browser the switches still save, and it says they run on the app', async () => {
    platform.name = 'web';
    api.setNearbySettings.mockResolvedValue({ ...adult, settings: { ...adult.settings, car: false } });
    draw();
    expect(await screen.findByTestId('nearby-offline-panel')).toHaveTextContent('These run on the Jarvis Android app.');
    await userEvent.click(await screen.findByRole('switch', { name: 'Car detection' }));
    expect(api.setNearbySettings).toHaveBeenCalledWith({ car: false });
    expect(nearby.apply).not.toHaveBeenCalled();
    expect(screen.queryByTestId('nearby-offline-mode')).not.toBeInTheDocument();
  });
});
