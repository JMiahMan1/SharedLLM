import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from './render';

/**
 * The wiring test: a stalled sensor must be visible on the pages people actually
 * look at. The banner's own logic is covered in `SensorStatusBanner.test.tsx` and
 * `lib/sensorStatus.test.ts`; what matters here is that both pages mount it, so a
 * regression that silently removes it is caught at the page level too.
 */

const mockState = vi.hoisted(() => ({
  sensors: {
    location: { enabled: true, permission: 'granted', message: null, recovering: false },
    steps: { enabled: true, permission: 'granted', message: null, recovering: false },
  } as Record<string, { enabled: boolean; permission: string; message: string | null; recovering: boolean }>,
  enableSensor: vi.fn(),
  openSensorSettings: vi.fn(),
}));

vi.mock('../context/LocationContext', async () => {
  // Partial mock: `renderWithProviders` mounts the real LocationProvider, but we
  // need to drive the sensor state directly rather than through plugins.
  const actual = await vi.importActual<typeof import('../context/LocationContext')>(
    '../context/LocationContext',
  );
  return {
    ...actual,
    useLocation: () => mockState,
    useBackgroundLocation: () => mockState,
  };
});

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

import Health from '../pages/Health';
import Wander from '../pages/Wander';

const healthy = () => {
  mockState.sensors = {
    location: { enabled: true, permission: 'granted', message: null, recovering: false },
    steps: { enabled: true, permission: 'granted', message: null, recovering: false },
  };
};

const stepsOff = () => {
  mockState.sensors = {
    location: { enabled: true, permission: 'granted', message: null, recovering: false },
    steps: { enabled: false, permission: 'granted', message: null, recovering: false },
  };
};

beforeEach(() => {
  vi.clearAllMocks();
  healthy();
  mockState.enableSensor.mockResolvedValue(true);
});

describe('Health page sensor visibility', () => {
  it('mounts the sensor banner', () => {
    renderWithProviders(<Health />);
    expect(screen.getByTestId('health-page')).toBeInTheDocument();
    stepsOff();
    renderWithProviders(<Health />);
    expect(screen.getAllByTestId('sensor-status-banner').length).toBeGreaterThan(0);
  });

  it('adds no banner at all when tracking is healthy', () => {
    renderWithProviders(<Health />);
    expect(screen.getByTestId('health-page')).toBeInTheDocument();
    expect(screen.queryByTestId('sensor-status-banner')).toBeNull();
  });

  it('warns that step tracking is off so a data gap is never mistaken for inactivity', () => {
    stepsOff();
    renderWithProviders(<Health />);
    expect(screen.getByTestId('sensor-notice-steps')).toBeInTheDocument();
  });
});

describe('Wander page sensor visibility', () => {
  it('mounts the sensor banner', () => {
    renderWithProviders(<Wander />);
    stepsOff();
    renderWithProviders(<Wander />);
    expect(screen.getAllByTestId('sensor-status-banner').length).toBeGreaterThan(0);
  });

  it('adds no banner at all when tracking is healthy', () => {
    renderWithProviders(<Wander />);
    expect(screen.queryByTestId('sensor-status-banner')).toBeNull();
  });

  it('warns that location sharing is off, which is the symptom on this page', () => {
    mockState.sensors = {
      location: { enabled: false, permission: 'granted', message: null, recovering: false },
      steps: { enabled: true, permission: 'granted', message: null, recovering: false },
    };
    renderWithProviders(<Wander />);
    const notice = screen.getByTestId('sensor-notice-location');
    expect(notice).toBeInTheDocument();
    expect(notice).toHaveAttribute('data-health', 'off');
  });
});