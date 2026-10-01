import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { SensorsState } from '../context/LocationContext';

/**
 * The regression these guard is the one that started this work: a phone that had
 * stopped reporting rendered stale/empty data and looked identical to a quiet
 * day. So the important assertion is not "the banner renders" — it is "the
 * banner renders NOTHING when healthy, and something specific when not".
 *
 * `vi.hoisted` rather than `vi.doMock` + dynamic import: the latter caches the
 * first mock for the whole file, which made these tests pass alone and fail
 * together.
 */

const healthySensors: SensorsState = {
  location: { enabled: true, permission: 'granted', message: null, recovering: false },
  steps: { enabled: true, permission: 'granted', message: null, recovering: false },
};

const mockState = vi.hoisted(() => ({
  sensors: {
    location: { enabled: true, permission: 'granted', message: null, recovering: false },
    steps: { enabled: true, permission: 'granted', message: null, recovering: false },
  } as Record<string, { enabled: boolean; permission: string; message: string | null; recovering: boolean }>,
  enableSensor: vi.fn(),
  openSensorSettings: vi.fn(),
}));

vi.mock('../context/LocationContext', () => ({
  useLocation: () => mockState,
}));

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

// Imported after the mock is registered so the component picks it up.
import { SensorStatusBanner } from '../components/location/SensorStatusBanner';

const setSensors = (next: Partial<SensorsState>) => {
  mockState.sensors = { ...healthySensors, ...next };
};

beforeEach(() => {
  vi.clearAllMocks();
  mockState.sensors = { ...healthySensors };
  mockState.enableSensor.mockResolvedValue(true);
  mockState.openSensorSettings.mockResolvedValue(undefined);
});

describe('SensorStatusBanner', () => {
  it('renders nothing at all when both sensors are healthy', () => {
    render(<SensorStatusBanner />);
    expect(screen.queryByTestId('sensor-status-banner')).toBeNull();
  });

  it('surfaces a sensor that is switched off, and says what the data gap costs', () => {
    setSensors({ steps: { enabled: false, permission: 'granted', message: null, recovering: false } });
    render(<SensorStatusBanner />);
    expect(screen.getByTestId('sensor-status-banner')).toBeInTheDocument();
    const notice = screen.getByTestId('sensor-notice-steps');
    // Scoped with `within` so we assert the copy lives inside *this* sensor's
    // notice rather than anywhere on the page.
    expect(within(notice).getByText(/step tracking is off/i)).toBeInTheDocument();
    expect(within(notice).getByText(/gaps/i)).toBeInTheDocument();
    // A healthy location sensor must not be listed.
    expect(screen.queryByTestId('sensor-notice-location')).toBeNull();
  });

  it('re-enables a disabled sensor from a single tap', async () => {
    setSensors({ location: { enabled: false, permission: 'granted', message: null, recovering: false } });
    render(<SensorStatusBanner />);
    await userEvent.click(screen.getByTestId('sensor-enable-location'));
    await waitFor(() => expect(mockState.enableSensor).toHaveBeenCalledWith('location'));
  });

  it('shows a denied sensor as an OS-settings problem, not as a retryable one', async () => {
    setSensors({
      location: { enabled: false, permission: 'denied', message: null, recovering: false },
    });
    render(<SensorStatusBanner />);
    expect(screen.getByTestId('sensor-notice-location')).toBeInTheDocument();
    expect(screen.getByTestId('sensor-settings-location')).toBeInTheDocument();
    // Crucially: do not offer a re-enable that cannot possibly work.
    expect(screen.queryByTestId('sensor-enable-location')).toBeNull();

    await userEvent.click(screen.getByTestId('sensor-settings-location'));
    await waitFor(() => expect(mockState.openSensorSettings).toHaveBeenCalledWith('location'));
  });

  it('reports unavailable hardware without offering any action', () => {
    setSensors({
      steps: {
        enabled: false,
        permission: 'unavailable',
        message: 'No hardware step counter',
        recovering: false,
      },
    });
    render(<SensorStatusBanner />);
    expect(screen.getByTestId('sensor-notice-steps')).toBeInTheDocument();
    expect(screen.getByText(/no hardware step counter/i)).toBeInTheDocument();
    expect(screen.queryByTestId('sensor-enable-steps')).toBeNull();
    expect(screen.queryByTestId('sensor-settings-steps')).toBeNull();
  });

  it('announces recovery politely instead of alarming, and offers no action', () => {
    setSensors({ steps: { enabled: true, permission: 'granted', message: null, recovering: true } });
    render(<SensorStatusBanner />);
    const notice = screen.getByTestId('sensor-notice-steps');
    expect(notice).toHaveAttribute('data-health', 'recovering');
    expect(within(notice).getByText(/is reconnecting/i)).toBeInTheDocument();
    expect(screen.queryByTestId('sensor-enable-steps')).toBeNull();
    // Announced by a screen reader before the visible text.
    expect(screen.getByText(/^Reconnecting:/)).toBeInTheDocument();
  });

  it('lists both sensors when both are unhealthy', () => {
    setSensors({
      location: { enabled: false, permission: 'granted', message: null, recovering: false },
      steps: { enabled: false, permission: 'granted', message: null, recovering: false },
    });
    render(<SensorStatusBanner />);
    expect(screen.getByTestId('sensor-notice-location')).toBeInTheDocument();
    expect(screen.getByTestId('sensor-notice-steps')).toBeInTheDocument();
  });

  it('gives every action a coarse-pointer tap target of at least 44px', () => {
    setSensors({
      location: { enabled: false, permission: 'denied', message: null, recovering: false },
      steps: { enabled: false, permission: 'granted', message: null, recovering: false },
    });
    render(<SensorStatusBanner />);
    for (const testId of ['sensor-enable-steps', 'sensor-settings-location']) {
      const button = screen.getByTestId(testId);
      expect(button.className).toContain('min-h-11');
      expect(button.className).toContain('pointer-coarse:min-h-11');
    }
  });

  it('does not double-report a failure: enableSensor owns the error toast', async () => {
    mockState.enableSensor.mockResolvedValue(false);
    setSensors({ steps: { enabled: false, permission: 'granted', message: null, recovering: false } });
    render(<SensorStatusBanner />);
    await userEvent.click(screen.getByTestId('sensor-enable-steps'));
    await waitFor(() => expect(mockState.enableSensor).toHaveBeenCalledWith('steps'));
  });

  it('clears the busy label again once enabling resolves', async () => {
    setSensors({ steps: { enabled: false, permission: 'granted', message: null, recovering: false } });
    render(<SensorStatusBanner />);
    const button = screen.getByTestId('sensor-enable-steps');
    await userEvent.click(button);
    await waitFor(() => expect(button).not.toBeDisabled());
  });
});