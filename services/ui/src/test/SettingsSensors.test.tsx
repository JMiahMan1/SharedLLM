import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import Settings from '../pages/Settings';
import { renderWithProviders } from './render';

const enableSensor = vi.fn(async () => true);
const disableSensor = vi.fn(async () => undefined);
const openSensorSettings = vi.fn(async () => undefined);

vi.mock('../context/LocationContext', async () => {
  const actual = await vi.importActual<typeof import('../context/LocationContext')>('../context/LocationContext');
  return {
    ...actual,
    useLocation: () => ({
      latitude: null,
      longitude: null,
      accuracy: null,
      speed: null,
      timestamp: null,
      isTracking: false,
      error: null,
      interval: 'stationary',
      sensors: {
        location: { enabled: true, permission: 'granted', message: null },
        steps: { enabled: false, permission: 'denied', message: 'Physical activity permission denied — steps turned off' },
      },
      enableSensor,
      disableSensor,
      openSensorSettings,
      startTracking: async () => undefined,
      stopTracking: () => undefined,
    }),
  };
});

describe('Settings sensors section', () => {
  beforeEach(() => {
    enableSensor.mockClear();
    disableSensor.mockClear();
    openSensorSettings.mockClear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders Sensors & Privacy with location and steps toggles', async () => {
    renderWithProviders(<Settings />);
    expect(await screen.findByText('Sensors & Privacy')).toBeInTheDocument();
    expect(screen.getByText('Location tracking')).toBeInTheDocument();
    expect(screen.getByText('Step counter')).toBeInTheDocument();
  });

  it('shows denial message and Open settings when steps permission denied', async () => {
    renderWithProviders(<Settings />);
    expect(
      await screen.findByText('Physical activity permission denied — steps turned off')
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /open settings/i })).toBeInTheDocument();
  });

  it('disables a sensor when toggled off', async () => {
    renderWithProviders(<Settings />);
    const locationSwitch = await screen.findByRole('switch', { name: 'Location tracking' });
    expect(locationSwitch).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(locationSwitch);
    await waitFor(() => expect(disableSensor).toHaveBeenCalledWith('location'));
  });

  it('enables steps when toggled on', async () => {
    renderWithProviders(<Settings />);
    const stepsSwitch = await screen.findByRole('switch', { name: 'Step counter' });
    expect(stepsSwitch).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(stepsSwitch);
    await waitFor(() => expect(enableSensor).toHaveBeenCalledWith('steps'));
  });
});
