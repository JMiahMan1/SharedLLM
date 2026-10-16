import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { UserWidgetSettings } from '../types/widget';

const authState = { user: { username: 'kid', is_admin: false }, role: 'user' };
vi.mock('../context/AuthContext', () => ({ useAuth: () => authState }));
vi.mock('../plugins/tokenBridge', () => ({ default: { refreshWidgets: vi.fn() } }));
vi.mock('../services/api', () => ({
  api: {
    getDeviceStates: vi.fn(),
    getDevices: vi.fn(async () => []),
    getUsers: vi.fn(async () => []),
    getUserPresence: vi.fn(),
    updateWidgetSettings: vi.fn(async () => ({ status: 'SUCCESS' })),
  },
  apiClient: { post: vi.fn(async () => ({ data: {} })) },
}));

import { api, apiClient } from '../services/api';
import DeviceControlWidget from '../components/widgets/DeviceControlWidget';

const settings = { widget_key: 'device_control', visibility: 'visible', order_index: 0, size: 'tall' } as UserWidgetSettings;

function renderWidget() {
  return render(<DeviceControlWidget settingsButton={null} userSettings={settings} onTogglePin={() => {}} />);
}

describe('DeviceControlWidget rooms', () => {
  beforeEach(() => {
    vi.mocked(api.getDeviceStates).mockResolvedValue([
      { entity_id: 'light.desk', friendly_name: 'Desk Lamp', state: 'off', domain: 'light', room: 'office' },
      { entity_id: 'switch.tv_plug', friendly_name: 'TV Plug', state: 'off', domain: 'switch' },
    ]);
    vi.mocked(apiClient.post).mockClear();
  });

  it('groups by Home Assistant area and never invents a room', async () => {
    renderWidget();
    fireEvent.click(await screen.findByRole('button', { name: 'Rooms' }));
    expect(await screen.findByText('Office (1)')).toBeInTheDocument();
    expect(screen.getByText('No room (1)')).toBeInTheDocument();
    // The old name heuristic filed "TV Plug" under a made-up "Living Room".
    expect(screen.queryByText(/Living Room/)).toBeNull();
  });

  it('asks before opening the garage when presence says away', async () => {
    vi.mocked(api.getDeviceStates).mockResolvedValue([
      { entity_id: 'cover.garage_door', friendly_name: 'Garage Door', state: 'closed', domain: 'cover' },
    ]);
    vi.mocked(api.getUserPresence).mockResolvedValue({ status: 'SUCCESS', user_id: 'kid', presence: { room: 'not_home', confidence: 1 } });
    const confirm = vi.fn(() => false);
    vi.stubGlobal('confirm', confirm);
    renderWidget();
    fireEvent.click(await screen.findByRole('button', { name: 'All (1)' }));
    fireEvent.click(await screen.findByRole('button', { name: /Open/ }));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(api.getUserPresence).toHaveBeenCalledWith('kid');
    expect(apiClient.post).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
