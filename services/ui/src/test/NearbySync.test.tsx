import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

const state = vi.hoisted(() => ({
  server: {} as Record<string, unknown>,
  device: { nearby: true, chat: true, offline: false, car: true, mine: true, scanPermission: true, connectPermission: true },
}));
const api = vi.hoisted(() => ({ getNearbySettings: vi.fn(), setNearbySettings: vi.fn() }));
const plugin = vi.hoisted(() => ({ getState: vi.fn(), apply: vi.fn(), requestPermissions: vi.fn() }));

vi.mock('@capacitor/core', () => ({ Capacitor: { getPlatform: () => 'android' } }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ token: 't', user: { username: 'casey' } }) }));
vi.mock('../services/api', () => ({ api }));
vi.mock('../plugins/nearby', () => ({ default: plugin }));

import NearbySync from '../components/NearbySync';
import { nearbyState } from '../lib/nearbySettings';

describe('NearbySync', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getNearbySettings.mockImplementation(async () => nearbyState(state.server));
    api.setNearbySettings.mockResolvedValue(undefined);
    plugin.getState.mockResolvedValue(state.device);
    plugin.apply.mockResolvedValue(state.device);
  });

  it('keeps and saves what the phone already runs when nothing is saved yet', async () => {
    state.server = { username: 'casey', settings: { nearby: false, chat: false, offline: false, car: true }, chosen: false };
    render(<NearbySync />);
    await waitFor(() => expect(api.setNearbySettings).toHaveBeenCalledWith({ nearby: true, chat: true, offline: false, car: true, mine: true }));
    expect(plugin.apply).not.toHaveBeenCalled();
  });

  it('applies a saved choice to the phone', async () => {
    state.server = { username: 'casey', settings: { nearby: false, chat: true, offline: false, car: true }, chosen: true };
    render(<NearbySync />);
    await waitFor(() => expect(plugin.apply).toHaveBeenCalledWith({ nearby: false, chat: true, offline: false, car: true, mine: true, ask: false }));
    expect(api.setNearbySettings).not.toHaveBeenCalled();
  });

  it("a child's phone takes the parents' settings even before any are saved", async () => {
    state.server = { username: 'casey', settings: { nearby: false, chat: false, offline: false, car: true }, chosen: false, managed_by_parent: true };
    render(<NearbySync />);
    await waitFor(() => expect(plugin.apply).toHaveBeenCalled());
    expect(api.setNearbySettings).not.toHaveBeenCalled();
  });

  it('an older server that does not say counts as a saved choice', () => {
    expect(nearbyState({ settings: {} }).chosen).toBe(true);
  });
});
