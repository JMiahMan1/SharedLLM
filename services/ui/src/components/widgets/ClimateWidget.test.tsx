import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import ClimateWidget from './ClimateWidget';
import { api } from '../../services/api';
import type { DeviceAssignment } from '../../types/api';
import type { DeviceEntry, UserWidgetSettings } from '../../types/widget';

const { updateWidgetConfig, authState } = vi.hoisted(() => ({
  updateWidgetConfig: vi.fn(),
  authState: { current: { username: 'abuser', role: 'user' } },
}));

vi.mock('../../stores/widgetStore', () => ({
  useWidgetStore: (selector: (state: { updateWidgetConfig: typeof updateWidgetConfig }) => unknown) =>
    selector({ updateWidgetConfig }),
}));

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: authState.current, role: authState.current.role }),
}));

vi.mock('../../hooks/useHaptics', () => ({
  useHaptics: () => ({ trigger: vi.fn(), isEnabled: () => true, setEnabled: vi.fn() }),
}));

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getDeviceStates: vi.fn().mockResolvedValue([]),
      callHaService: vi.fn().mockResolvedValue({ status: 'ok' }),
      getDevices: vi.fn().mockResolvedValue([]),
    },
  };
});

const baseAttrs = {
  current_temperature: 20,
  temperature: 22,
  hvac_mode: 'heat',
  hvac_action: 'heating',
  hvac_modes: ['off', 'heat', 'cool', 'auto'],
  preset_modes: ['away', 'home'],
  preset_mode: 'home',
  min_temp: 16,
  max_temp: 30,
  target_temp_step: 0.5,
  humidity: 44,
  friendly_name: 'Living Room',
};

const livingRoom = (overrides: Partial<DeviceEntry> = {}): DeviceEntry => ({
  entity_id: 'climate.living_room',
  friendly_name: 'Living Room',
  domain: 'climate',
  state: 'heat',
  attributes: { ...baseAttrs },
  ...overrides,
});

const settings = (overrides: Partial<UserWidgetSettings> = {}): UserWidgetSettings => ({
  widget_key: 'climate',
  visibility: 'visible',
  order_index: 0,
  size: 'medium',
  is_pinned: false,
  sort_mode: null,
  pinned_devices: [],
  config: {},
  updated_at: 0,
  ...overrides,
});

const renderWidget = (overrides: Partial<UserWidgetSettings> = {}) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <ClimateWidget
      settingsButton={<span data-testid="settings-button" />}
      userSettings={settings(overrides)}
      onTogglePin={() => {}}
    />
  );
  // The client is handed back so a test can invalidate the poll and observe
  // what the widget does when the listing changes underneath it.
  return { queryClient, ...render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>) };
};

const mockedApi = vi.mocked(api);
const assignment = (device_id: string, username: string): DeviceAssignment => ({
  id: 1, device_id, user_id: 2, username,
});

beforeEach(() => {
  authState.current = { username: 'abuser', role: 'user' };
  mockedApi.getDeviceStates.mockResolvedValue([livingRoom()]);
  mockedApi.getDevices.mockResolvedValue([assignment('climate.living_room', 'abuser')]);
  mockedApi.callHaService.mockReset().mockResolvedValue({ status: 'ok' });
  updateWidgetConfig.mockReset().mockResolvedValue(undefined);
});

describe('ClimateWidget', () => {
  it('shows the live setpoint, status and controls for one configured device', async () => {
    renderWidget({ config: { devices: ['climate.living_room'] } });

    expect(await screen.findByText('Heating · 44% RH')).toBeInTheDocument();
    expect(screen.getByText('Living Room')).toBeInTheDocument();
    expect(screen.getByText('22')).toBeInTheDocument();
    expect(screen.getByText('Humidity 44%')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Raise target/ })).toBeEnabled();
    // Preset advertised by the entity, not hard-coded.
    expect(screen.getByRole('button', { name: 'Away' })).toBeInTheDocument();
  });

  it('coalesces rapid stepper taps into one snapped set_temperature call', async () => {
    renderWidget({ config: { devices: ['climate.living_room'] } });

    const up = await screen.findByRole('button', { name: /Raise target/ });
    fireEvent.click(up);
    fireEvent.click(up);

    await waitFor(() => expect(mockedApi.callHaService).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(mockedApi.callHaService).toHaveBeenCalledWith(
      'climate', 'set_temperature', 'climate.living_room', { temperature: 23 }
    );
  });

  it('changes the hvac mode through the mode row', async () => {
    renderWidget({ config: { devices: ['climate.living_room'] } });

    fireEvent.click(await screen.findByRole('button', { name: 'Off' }));

    await waitFor(() => expect(mockedApi.callHaService).toHaveBeenCalledWith(
      'climate', 'set_hvac_mode', 'climate.living_room', { hvac_mode: 'off' }
    ));
  });

  it('trusts the server-filtered listing instead of re-deriving permission', async () => {
    // An entity protection permit exists with no DeviceAssignment row behind
    // it. The old client-side check compared against assignments and would
    // have wrongly locked this thermostat; the backend already decided the
    // caller may see and control it, so the widget must not second-guess.
    mockedApi.getDevices.mockResolvedValue([assignment('climate.living_room', 'someone.else')]);
    renderWidget({ config: { devices: ['climate.living_room'] } });

    const up = await screen.findByRole('button', { name: /Raise target/ });
    expect(up).toBeEnabled();
    fireEvent.click(up);

    await waitFor(() => expect(mockedApi.callHaService).toHaveBeenCalledWith(
      'climate', 'set_temperature', 'climate.living_room', { temperature: 22.5 }
    ));
  });

  it('refuses a control for an entity that has left the filtered listing', async () => {
    const { queryClient } = renderWidget({ config: { devices: ['climate.living_room'] } });
    await screen.findByTestId('climate-dial');

    // The entity is pulled out from under us — a lock was just applied, so the
    // next poll no longer returns it. Nothing should be left to press.
    mockedApi.getDeviceStates.mockResolvedValue([]);
    await queryClient.invalidateQueries({ queryKey: ['widget', 'climate', 'states'] });
    await waitFor(() => expect(screen.queryByTestId('climate-dial')).not.toBeInTheDocument());
    expect(screen.getByText('Configured device unavailable')).toBeInTheDocument();
    // The withheld id must not be echoed back: the listing hid it on purpose.
    expect(screen.queryByText('climate.living_room')).not.toBeInTheDocument();
    expect(mockedApi.callHaService).not.toHaveBeenCalled();
  });

  it('writes both bounds together for a heat_cool thermostat', async () => {
    mockedApi.getDeviceStates.mockResolvedValue([livingRoom({
      state: 'heat_cool',
      attributes: {
        ...baseAttrs, hvac_mode: 'heat_cool', hvac_action: 'idle',
        target_temp_low: 20, target_temp_high: 25,
      } as DeviceEntry['attributes'],
    })]);
    renderWidget({ config: { devices: ['climate.living_room'] } });

    fireEvent.click(await screen.findByRole('button', { name: /Raise cool setpoint/ }));

    await waitFor(() => expect(mockedApi.callHaService).toHaveBeenCalledWith(
      'climate', 'set_temperature', 'climate.living_room', { target_temp_low: 20, target_temp_high: 25.5 }
    ));
  });

  it('shows room tiles once several devices are configured and opens a dial on tap', async () => {
    mockedApi.getDeviceStates.mockResolvedValue([
      livingRoom(),
      livingRoom({
        entity_id: 'climate.bedroom',
        friendly_name: 'Bedroom',
        state: 'off',
        attributes: { ...baseAttrs, friendly_name: 'Bedroom', hvac_mode: 'off', hvac_action: 'off' },
      }),
    ]);
    renderWidget({ config: { devices: ['climate.living_room', 'climate.bedroom'] } });

    const tiles = await screen.findAllByTestId('climate-tile');
    expect(tiles).toHaveLength(2);
    expect(screen.queryByTestId('climate-dial')).not.toBeInTheDocument();

    fireEvent.click(tiles[0]);
    expect(await screen.findByTestId('climate-dial')).toHaveAttribute('data-entity', 'climate.living_room');
  });

  it('asks which devices to track instead of inventing them when nothing is configured', async () => {
    renderWidget({ config: {} });

    expect(await screen.findByText('No climate devices selected')).toBeInTheDocument();
    expect(mockedApi.callHaService).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Devices' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /Living Room/ }));
    fireEvent.click(screen.getByRole('radio', { name: 'Tiles' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(updateWidgetConfig).toHaveBeenCalledWith(
      'climate', { devices: ['climate.living_room'], layout: 'tiles' }
    ));
  });
});

describe('ClimateWidget mobile parity', () => {
  // The dashboard ships to Android/iOS from this same tree, so the dial must be
  // usable by thumb at phone widths: 44px targets and a ring that scales with
  // its ~280px grid column rather than a fixed pixel canvas.
  it('gives every dial control a 44px touch target', async () => {
    renderWidget({ config: { devices: ['climate.living_room'] } });

    const dial = await screen.findByTestId('climate-dial');
    const controls = within(dial).getAllByRole('button');
    expect(controls.length).toBeGreaterThan(2);
    for (const control of controls) {
      expect(control.className).toMatch(/pointer-coarse:(min-h-11|h-11)/);
    }
  });

  it('draws the ring from a viewBox so it fits a 280px column', async () => {
    renderWidget({ config: { devices: ['climate.living_room'] } });

    await screen.findByTestId('climate-dial');
    // Query the ring specifically: the dial also holds steppers, whose icons
    // are ordinary 24px svgs.
    const svg = screen.getByTestId('climate-ring');
    expect(svg.getAttribute('viewBox')).toBe('0 0 200 200');
    const ring = svg.parentElement as HTMLElement;
    expect(ring.className).toMatch(/aspect-square/);
    expect(ring.className).toMatch(/w-full/);
  });
});
