import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { LocationProvider, useLocation } from '../context/LocationContext';

type Availability = { available: boolean; permissionRequired?: boolean; permissionGranted?: boolean };

const storageGet = vi.fn<(key: string) => Promise<string | null>>(async () => null);
const storageSet = vi.fn<(key: string, value: string) => Promise<void>>(async () => undefined);

vi.mock('../lib/storage', () => ({
  storageGet: (key: string) => storageGet(key),
  storageSet: (key: string, value: string) => storageSet(key, value),
  storageGetSync: () => null,
  storageInit: async () => undefined,
  storageRemove: async () => undefined,
  storageClear: async () => undefined,
}));

const KEY_STEPS = 'jarvis_sensor_steps_enabled';
const KEY_LOCATION = 'jarvis_sensor_location_enabled';

const startPolling = vi.fn(async () => undefined);
const addListener = vi.fn(async () => ({ remove: async () => undefined }));
const getTodaySteps = vi.fn(async () => ({ available: true, steps: 1234 }));
const isAvailable = vi.fn<() => Promise<Availability>>(async () => ({ available: true, permissionGranted: true }));
const requestPermission = vi.fn(async () => ({ granted: true }));

vi.mock('../plugins/stepCounter', () => ({
  default: {
    isAvailable: () => isAvailable(),
    requestPermission: () => requestPermission(),
    getTodaySteps: () => getTodaySteps(),
    getDayHistory: async () => ({ days: [], source: 'phone' }),
    getDaysSince: async () => ({ days: [], source: 'phone' }),
    startPolling: () => startPolling(),
    stopPolling: async () => undefined,
    openSettings: async () => undefined,
    addListener: () => addListener(),
  },
}));

vi.mock('../plugins/tokenBridge', () => ({
  default: { getToken: async () => null, setToken: async () => undefined },
}));

const watchPosition = vi.fn(async () => 'watch-1');
const checkPermissions = vi.fn(async () => ({ location: 'prompt' }));
const requestPermissions = vi.fn(async () => ({ location: 'prompt' as string }));

vi.mock('@capacitor/geolocation', () => ({
  Geolocation: {
    watchPosition: () => watchPosition(),
    getCurrentPosition: async () => ({ coords: { latitude: 33.16, longitude: -111.56, accuracy: 10, speed: 0 } }),
    clearWatch: vi.fn(),
    checkPermissions: () => checkPermissions(),
    requestPermissions: () => requestPermissions(),
  },
}));

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: () => true, getPlatform: () => 'android' },
}));

vi.mock('../lib/serverUrl', () => ({ getServerOrigin: () => 'http://localhost' }));

/** Renders the real provider and exposes its sensor state. */
function Probe() {
  const { sensors } = useLocation();
  return (
    <div>
      <span data-testid="steps-enabled">{String(sensors.steps.enabled)}</span>
      <span data-testid="steps-message">{sensors.steps.message ?? ''}</span>
      <span data-testid="location-enabled">{String(sensors.location.enabled)}</span>
      <span data-testid="location-message">{sensors.location.message ?? ''}</span>
    </div>
  );
}

function renderProvider() {
  return render(
    <LocationProvider>
      <Probe />
    </LocationProvider>,
  );
}

/** Did the code persist a permanent off-state for this sensor? */
function persistedOff(key: string): boolean {
  return storageSet.mock.calls.some(([k, v]) => k === key && v === 'false');
}

describe('LocationContext sensor self-heal', () => {
  beforeEach(() => {
    storageGet.mockReset();
    storageGet.mockResolvedValue(null);
    storageSet.mockReset();
    storageSet.mockResolvedValue(undefined);
    startPolling.mockReset();
    startPolling.mockResolvedValue(undefined);
    addListener.mockReset();
    addListener.mockResolvedValue({ remove: async () => undefined });
    getTodaySteps.mockReset();
    getTodaySteps.mockResolvedValue({ available: true, steps: 1234 });
    isAvailable.mockReset();
    isAvailable.mockResolvedValue({ available: true, permissionGranted: true });
    requestPermission.mockReset();
    requestPermission.mockResolvedValue({ granted: true });
    watchPosition.mockReset();
    watchPosition.mockResolvedValue('watch-1');
    checkPermissions.mockReset();
    checkPermissions.mockResolvedValue({ location: 'prompt' });
    requestPermissions.mockReset();
    requestPermissions.mockResolvedValue({ location: 'prompt' });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('does NOT persist a permanent disable when startPolling throws transiently', async () => {
    // The bug: any startPolling failure disabled steps forever.
    startPolling.mockRejectedValue(new Error('bridge not ready'));

    renderProvider();

    await waitFor(() => expect(startPolling).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId('steps-message').textContent).toMatch(/retrying automatically/i),
    );

    expect(persistedOff(KEY_STEPS)).toBe(false);
    expect(screen.getByTestId('steps-enabled').textContent).toBe('true');
  });

  it('does NOT treat a message merely mentioning "permission" as a denial', async () => {
    // The old substring match killed tracking for this class of error.
    startPolling.mockRejectedValue(new Error('Activity Recognition permission bridge timed out'));

    renderProvider();

    await waitFor(() => expect(startPolling).toHaveBeenCalled());
    expect(persistedOff(KEY_STEPS)).toBe(false);
    expect(screen.getByTestId('steps-message').textContent).toMatch(/retrying automatically/i);
  });

  it('keeps steps enabled when the counter read throws', async () => {
    getTodaySteps.mockRejectedValue(new Error('permission service unavailable'));

    renderProvider();

    await waitFor(() => expect(screen.getByTestId('steps-message').textContent).not.toBe(''));
    expect(persistedOff(KEY_STEPS)).toBe(false);
  });

  it('still honours a genuine, explicit permission denial', async () => {
    // Correct behaviour must be preserved: a real refusal does turn it off.
    isAvailable.mockResolvedValue({ available: true, permissionRequired: true, permissionGranted: false });
    requestPermission.mockResolvedValue({ granted: false });

    renderProvider();

    await waitFor(() => expect(persistedOff(KEY_STEPS)).toBe(true));
    expect(screen.getByTestId('steps-enabled').textContent).toBe('false');
  });

  it('reports "unavailable" (not "denied") when the device has no pedometer', async () => {
    isAvailable.mockResolvedValue({ available: false });

    renderProvider();

    await waitFor(() =>
      expect(screen.getByTestId('steps-message').textContent).toMatch(/no hardware step counter/i),
    );
  });

  it('does NOT persist a location disable for an uncoded permission-sounding error', async () => {
    checkPermissions.mockResolvedValue({ location: 'prompt' });
    watchPosition.mockRejectedValue(new Error('Access denied by the platform bridge'));

    renderProvider();

    await waitFor(() => expect(watchPosition).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId('location-message').textContent).toMatch(/retrying automatically/i),
    );
    expect(persistedOff(KEY_LOCATION)).toBe(false);
    expect(screen.getByTestId('location-enabled').textContent).toBe('true');
  });

  it('still honours a coded PERMISSION_DENIED from the OS', async () => {
    // An explicit refusal from the OS, then again from the prompt: this is the
    // only shape that legitimately turns tracking off.
    checkPermissions.mockResolvedValue({ location: 'denied' });
    requestPermissions.mockResolvedValue({ location: 'denied' });

    renderProvider();

    await waitFor(() => expect(persistedOff(KEY_LOCATION)).toBe(true));
  });
});