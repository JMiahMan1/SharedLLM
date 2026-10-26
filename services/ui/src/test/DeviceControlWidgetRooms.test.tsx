import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { UserWidgetSettings } from '../types/widget';

const authState = { user: { username: 'kid', is_admin: false }, role: 'user', can: () => false };
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
import { confirmDialog } from '../components/ui/confirmDialog';

// Destructive actions draw the app's own dialog now, not the browser's.
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
const confirmMock = vi.mocked(confirmDialog);

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
    const confirm = confirmMock;
    confirm.mockResolvedValue(false);
    renderWidget();
    fireEvent.click(await screen.findByRole('button', { name: 'All (1)' }));
    fireEvent.click(await screen.findByRole('button', { name: /Open/ }));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(api.getUserPresence).toHaveBeenCalledWith('kid');
    expect(apiClient.post).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('uses the thermostat own modes and repaints after a mode command', async () => {
    const attrs = {
      friendly_name: 'Upstairs',
      hvac_mode: 'heat',
      hvac_modes: ['off', 'heat', 'cool'],
      temperature: 70,
      current_temperature: 68,
      min_temp: 50,
      max_temp: 90,
      target_temp_step: 1,
    };
    vi.mocked(api.getDeviceStates)
      .mockResolvedValueOnce([{
        entity_id: 'climate.upstairs', friendly_name: 'Upstairs', state: 'heat', domain: 'climate', room: 'Upstairs', attributes: attrs,
      }])
      .mockResolvedValue([{
        entity_id: 'climate.upstairs', friendly_name: 'Upstairs', state: 'heat', domain: 'climate', room: 'Upstairs',
        attributes: { ...attrs, hvac_mode: 'heat' },
      }]);

    renderWidget();
    fireEvent.click(await screen.findByRole('button', { name: 'All (1)' }));
    const [deviceName] = await screen.findAllByText('Upstairs');
    fireEvent.click(deviceName);

    expect(await screen.findByText('Climate Thermostat Controls')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Heat' })).toHaveAttribute('aria-pressed', 'true');
    // The dialog must not invent a mode the thermostat never advertised.
    expect(screen.queryByRole('button', { name: 'Heat/Cool' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Off' }));

    // The panel paints the requested mode before the service returns.
    expect(screen.getByRole('button', { name: 'Off' })).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith('/execute/ha_service', expect.objectContaining({
      domain: 'climate',
      service: 'set_hvac_mode',
      entity_id: 'climate.upstairs',
      service_data: { hvac_mode: 'off' },
    })));
    // The follow-up load still reports heat; the optimistic patch must hold.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Off' })).toHaveAttribute('aria-pressed', 'true'));
  });
});
