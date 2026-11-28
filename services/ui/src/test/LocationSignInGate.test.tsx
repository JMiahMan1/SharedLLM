import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { LocationProvider, useLocation } from '../context/LocationContext';
import { AuthContext, type AuthContextType } from '../context/AuthContext';

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

const startPolling = vi.fn(async () => undefined);
const stopPolling = vi.fn(async () => undefined);
const stepRequestPermission = vi.fn(async () => ({ granted: true }));
const isAvailable = vi.fn<() => Promise<Availability>>(async () => ({ available: true, permissionGranted: true }));

vi.mock('../plugins/stepCounter', () => ({
  default: {
    isAvailable: () => isAvailable(),
    requestPermission: () => stepRequestPermission(),
    getTodaySteps: async () => ({ available: true, steps: 1234 }),
    getDayHistory: async () => ({ days: [], source: 'phone' }),
    getDaysSince: async () => ({ days: [], source: 'phone' }),
    startPolling: () => startPolling(),
    stopPolling: () => stopPolling(),
    openSettings: async () => undefined,
    addListener: async () => ({ remove: async () => undefined }),
  },
}));

vi.mock('../plugins/tokenBridge', () => ({
  default: { getToken: async () => null, setToken: async () => undefined },
}));

const watchPosition = vi.fn(async () => 'watch-1');
const clearWatch = vi.fn<(options?: unknown) => Promise<void>>(async () => undefined);
const checkPermissions = vi.fn(async () => ({ location: 'prompt' }));
const requestPermissions = vi.fn(async () => ({ location: 'prompt' as string }));

vi.mock('@capacitor/geolocation', () => ({
  Geolocation: {
    watchPosition: () => watchPosition(),
    getCurrentPosition: async () => ({ coords: { latitude: 39.45, longitude: -104.48, accuracy: 10, speed: 0 } }),
    clearWatch: (options?: { id?: string }) => clearWatch(options as never),
    checkPermissions: () => checkPermissions(),
    requestPermissions: () => requestPermissions(),
  },
}));

const startBackgroundTracking = vi.fn(async () => undefined);
const stopBackgroundTracking = vi.fn(async () => undefined);

vi.mock('../lib/locationTracking', () => ({
  startBackgroundTracking: () => startBackgroundTracking(),
  stopBackgroundTracking: () => stopBackgroundTracking(),
}));

const startBackgroundSteps = vi.fn<(user: string) => Promise<void>>(async () => undefined);
const stopBackgroundSteps = vi.fn(async () => undefined);

vi.mock('../lib/stepSync', () => ({
  startBackgroundSteps: (user: string) => startBackgroundSteps(user),
  stopBackgroundSteps: () => stopBackgroundSteps(),
}));

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: () => true, getPlatform: () => 'android' },
}));

vi.mock('../lib/serverUrl', () => ({ getServerOrigin: () => 'http://localhost' }));

const KEY_STEPS = 'jarvis_sensor_steps_enabled';
const KEY_LOCATION = 'jarvis_sensor_location_enabled';
const KEY_LEGACY = 'jarvis_location_tracking_enabled';

/** Every key the provider owns. A sign-out must not touch any of them. */
const PREFERENCE_KEYS = [KEY_LOCATION, KEY_STEPS, KEY_LEGACY];

function Probe() {
  const { sensors } = useLocation();
  return (
    <div>
      <span data-testid="location-enabled">{String(sensors.location.enabled)}</span>
      <span data-testid="location-permission">{sensors.location.permission}</span>
      <span data-testid="steps-enabled">{String(sensors.steps.enabled)}</span>
      <span data-testid="steps-permission">{sensors.steps.permission}</span>
    </div>
  );
}

function authValue(token: string | null): AuthContextType {
  return {
    user: token ? ({ id: 1, username: 'tester', display_name: 'Tester' } as never) : null,
    token,
    role: token ? 'user' : null,
    can: () => true,
    login: async () => undefined,
    logout: async () => undefined,
    isLoading: false,
    initError: null,
    refreshProfile: async () => undefined,
  };
}

function view(token: string | null) {
  return (
    <AuthContext.Provider value={authValue(token)}>
      <LocationProvider>
        <Probe />
      </LocationProvider>
    </AuthContext.Provider>
  );
}

/** Did anything write a "this person turned it off" preference? */
function wroteAnyPreference(): boolean {
  return storageSet.mock.calls.some(([key]) => PREFERENCE_KEYS.includes(key));
}

describe('LocationContext waits for a session', () => {
  beforeEach(() => {
    storageGet.mockReset();
    storageGet.mockResolvedValue(null);
    storageSet.mockReset();
    storageSet.mockResolvedValue(undefined);
    startPolling.mockReset();
    startPolling.mockResolvedValue(undefined);
    stopPolling.mockReset();
    stopPolling.mockResolvedValue(undefined);
    stepRequestPermission.mockReset();
    stepRequestPermission.mockResolvedValue({ granted: true });
    isAvailable.mockReset();
    isAvailable.mockResolvedValue({ available: true, permissionGranted: true });
    watchPosition.mockReset();
    watchPosition.mockResolvedValue('watch-1');
    clearWatch.mockReset();
    clearWatch.mockResolvedValue(undefined);
    checkPermissions.mockReset();
    checkPermissions.mockResolvedValue({ location: 'prompt' });
    requestPermissions.mockReset();
    // The person answers the dialog: only then may the watch start.
    requestPermissions.mockResolvedValue({ location: 'granted' });
    startBackgroundTracking.mockReset();
    startBackgroundTracking.mockResolvedValue(undefined);
    stopBackgroundTracking.mockReset();
    stopBackgroundTracking.mockResolvedValue(undefined);
    startBackgroundSteps.mockReset();
    startBackgroundSteps.mockResolvedValue(undefined);
    stopBackgroundSteps.mockReset();
    stopBackgroundSteps.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('starts nothing and asks for nothing before there is a session', async () => {
    // The bug this guards: a fresh install started tracking on the login
    // screen, so the location dialog belonged to no account.
    const { container } = render(view(null));

    await waitFor(() => expect(container).toBeTruthy());
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(watchPosition).not.toHaveBeenCalled();
    expect(requestPermissions).not.toHaveBeenCalled();
    expect(startPolling).not.toHaveBeenCalled();
    expect(stepRequestPermission).not.toHaveBeenCalled();
    expect(startBackgroundTracking).not.toHaveBeenCalled();
    expect(startBackgroundSteps).not.toHaveBeenCalled();
    expect(screen.getByTestId('location-enabled').textContent).toBe('false');
    expect(screen.getByTestId('steps-enabled').textContent).toBe('false');
    expect(screen.getByTestId('location-permission').textContent).toBe('disabled');
    expect(screen.getByTestId('steps-permission').textContent).toBe('disabled');
  });

  it('starts the sensors the preferences ask for once someone signs in', async () => {
    storageGet.mockResolvedValue(null); // native first run: both default on
    const { rerender } = render(view(null));

    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(watchPosition).not.toHaveBeenCalled();

    rerender(view('api-key-123'));

    await waitFor(() => expect(watchPosition).toHaveBeenCalled());
    await waitFor(() => expect(startPolling).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId('location-enabled').textContent).toBe('true'));
    expect(screen.getByTestId('steps-enabled').textContent).toBe('true');
  });

  it('leaves a sensor that the person turned off alone on sign-in', async () => {
    storageGet.mockImplementation(async (key: string) => (key === KEY_LOCATION ? 'false' : null));
    const { rerender } = render(view(null));

    rerender(view('api-key-123'));

    await waitFor(() => expect(startPolling).toHaveBeenCalled());
    expect(watchPosition).not.toHaveBeenCalled();
    expect(screen.getByTestId('location-enabled').textContent).toBe('false');
  });

  it('stops everything on sign-out without recording a choice nobody made', async () => {
    const { rerender } = render(view('api-key-123'));

    await waitFor(() => expect(watchPosition).toHaveBeenCalled());
    storageSet.mockClear();

    rerender(view(null));

    await waitFor(() => expect(clearWatch).toHaveBeenCalled());
    await waitFor(() => expect(stopPolling).toHaveBeenCalled());
    await waitFor(() => expect(stopBackgroundTracking).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId('location-enabled').textContent).toBe('false'));
    expect(screen.getByTestId('steps-enabled').textContent).toBe('false');

    // Signing out is not the person turning the switches off: the preference
    // has to survive so the next sign-in starts where they left it.
    expect(wroteAnyPreference()).toBe(false);
  });

  it('starts the sensors again when the same phone signs back in', async () => {
    const { rerender } = render(view('api-key-123'));
    await waitFor(() => expect(watchPosition).toHaveBeenCalled());

    rerender(view(null));
    await waitFor(() => expect(clearWatch).toHaveBeenCalled());

    watchPosition.mockClear();
    startPolling.mockClear();
    rerender(view('api-key-123'));

    await waitFor(() => expect(watchPosition).toHaveBeenCalled());
    await waitFor(() => expect(startPolling).toHaveBeenCalled());
  });

  it('still runs the sensors for an isolated render with no auth provider', async () => {
    // Every existing test and every embedded use renders the provider alone.
    render(
      <LocationProvider>
        <Probe />
      </LocationProvider>,
    );

    await waitFor(() => expect(watchPosition).toHaveBeenCalled());
    await waitFor(() => expect(startPolling).toHaveBeenCalled());
    expect(screen.getByTestId('location-enabled').textContent).toBe('true');
  });
});
