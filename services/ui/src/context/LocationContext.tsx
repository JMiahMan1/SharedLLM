import { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Capacitor } from '@capacitor/core';
import { Geolocation } from '@capacitor/geolocation';
import toast from 'react-hot-toast';
import { storageGet, storageSet } from '../lib/storage';
import { getServerOrigin } from '../lib/serverUrl';
import StepCounter from '../plugins/stepCounter';
import TokenBridge from '../plugins/tokenBridge';

export type SensorId = 'location' | 'steps';
export type SensorPermissionStatus = 'unknown' | 'granted' | 'denied' | 'unavailable' | 'disabled';

interface LocationState {
  latitude: number | null;
  longitude: number | null;
  accuracy: number | null;
  speed: number | null;
  timestamp: number | null;
  isTracking: boolean;
  error: string | null;
  interval: 'stationary' | 'transit';
}

interface SensorToggleState {
  enabled: boolean;
  permission: SensorPermissionStatus;
  message: string | null;
}

export interface SensorsState {
  location: SensorToggleState;
  steps: SensorToggleState;
}

interface LocationContextValue extends LocationState {
  sensors: SensorsState;
  enableSensor: (id: SensorId) => Promise<boolean>;
  disableSensor: (id: SensorId) => Promise<void>;
  openSensorSettings: (id: SensorId) => Promise<void>;
  startTracking: () => Promise<void>;
  stopTracking: () => void;
}

/* eslint-disable react-refresh/only-export-components */
export const LocationContext = createContext<LocationContextValue | null>(null);

const SPEED_THRESHOLD_MPH = 15;
const GEOFENCE_RADIUS_M = 200; // ~1/8 mile — don't log routes under this when stationary
const DAILY_STEPS_SYNC_INTERVAL_MS = 30000; // sync steps at least every 30s when stationary
const KEY_LOCATION_ENABLED = 'jarvis_sensor_location_enabled';
const KEY_STEPS_ENABLED = 'jarvis_sensor_steps_enabled';

function logSensor(scope: string, message: string, err?: unknown) {
  if (err !== undefined) {
    console.error(`[sensors:${scope}] ${message}`, err);
  } else {
    console.warn(`[sensors:${scope}] ${message}`);
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export function LocationProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<LocationState>({
    latitude: null,
    longitude: null,
    accuracy: null,
    speed: null,
    timestamp: null,
    isTracking: false,
    error: null,
    interval: 'stationary',
  });

  const [sensors, setSensors] = useState<SensorsState>({
    location: { enabled: false, permission: 'unknown', message: null },
    steps: { enabled: false, permission: 'unknown', message: null },
  });

  const lastLocationRef = useRef<{ lat: number; lng: number } | null>(null);
  const lastFixRef = useRef<{ lat: number; lng: number; t: number } | null>(null);
  const watchIdRef = useRef<string | number | null>(null);
  const startingRef = useRef(false);
  const intervalRef = useRef<'stationary' | 'transit'>('stationary');
  const dailyStepsRef = useRef<number | null>(null);
  const lastSyncedStepsRef = useRef<number | null>(null);
  const stepsPluginReadyRef = useRef(false);
  const stepUpdateListenerRef = useRef<{ remove: () => Promise<void> } | null>(null);
  const stationarySyncTimerRef = useRef<number | null>(null);
  const sensorsRef = useRef(sensors);
  useEffect(() => {
    sensorsRef.current = sensors;
  }, [sensors]);

  const calculateDistance = useCallback((lat1: number, lng1: number, lat2: number, lng2: number) => {
    const R = 6371e3;
    const φ1 = (lat1 * Math.PI) / 180;
    const φ2 = (lat2 * Math.PI) / 180;
    const Δφ = ((lat2 - lat1) * Math.PI) / 180;
    const Δλ = ((lng2 - lng1) * Math.PI) / 180;
    const a = Math.sin(Δφ / 2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }, []);

  const patchSensor = useCallback((id: SensorId, patch: Partial<SensorToggleState>) => {
    // Keep sensorsRef in sync immediately so startTracking/sync gates see the
    // new value before React re-renders (async enable paths await storage I/O).
    setSensors((prev) => {
      const next = {
        ...prev,
        [id]: { ...prev[id], ...patch },
      };
      sensorsRef.current = next;
      return next;
    });
  }, []);

  const stopStepService = useCallback(async () => {
    try {
      await StepCounter.stopPolling();
    } catch (err) {
      logSensor('steps', 'stopPolling failed', err);
    }
    if (stepUpdateListenerRef.current) {
      try {
        await stepUpdateListenerRef.current.remove();
      } catch (err) {
        logSensor('steps', 'listener remove failed', err);
      }
      stepUpdateListenerRef.current = null;
    }
    stepsPluginReadyRef.current = false;
    dailyStepsRef.current = null;
    lastSyncedStepsRef.current = null;
  }, []);

  const refreshDailySteps = useCallback(async () => {
    if (!sensorsRef.current.steps.enabled) return;
    try {
      if (!stepsPluginReadyRef.current) {
        const avail = await StepCounter.isAvailable();
        if (!avail.available) {
          patchSensor('steps', {
            permission: 'unavailable',
            message: 'No hardware step counter on this device',
          });
          return;
        }
        if (avail.permissionRequired && !avail.permissionGranted) {
          const req = await StepCounter.requestPermission();
          if (!req.granted) {
            // Denial turns the feature OFF (still re-enableable from Settings)
            patchSensor('steps', {
              enabled: false,
              permission: 'denied',
              message: req.permanentlyDenied
                ? 'Step permission blocked — enable it in system Settings'
                : 'Physical activity permission denied — steps turned off',
            });
            await storageSet(KEY_STEPS_ENABLED, 'false');
            await stopStepService();
            toast.error('Step tracking disabled — permission denied');
            logSensor('steps', 'permission denied by user');
            return;
          }
        }
        stepsPluginReadyRef.current = true;
      }
      const reading = await StepCounter.getTodaySteps();
      if (reading.available && typeof reading.steps === 'number' && reading.steps >= 0) {
        dailyStepsRef.current = reading.steps;
        patchSensor('steps', { permission: 'granted', message: null });
      }
    } catch (err) {
      const msg = errorMessage(err);
      if (msg.includes('permission')) {
        patchSensor('steps', {
          enabled: false,
          permission: 'denied',
          message: 'Physical activity permission denied — steps turned off',
        });
        await storageSet(KEY_STEPS_ENABLED, 'false');
        await stopStepService();
        toast.error('Step tracking disabled — permission denied');
      } else {
        patchSensor('steps', { message: msg });
      }
      logSensor('steps', 'refresh failed', err);
    }
  }, [patchSensor, stopStepService]);

  // Sync steps independently of location updates (treadmill/stationary use)
  const syncDailySteps = useCallback(async () => {
    if (!sensorsRef.current.steps.enabled) return;
    try {
      const token = await storageGet('jarvis_api_key');
      const rawServerUrl = await storageGet('jarvis_server_url');
      const serverUrl = rawServerUrl || getServerOrigin();
      if (!token || !serverUrl) {
        logSensor('steps', 'sync skipped: missing token or server URL');
        return;
      }
      if (dailyStepsRef.current === null || dailyStepsRef.current === lastSyncedStepsRef.current) return;

      let user: string;
      const rawUser = await storageGet('jarvis_user');
      if (rawUser) {
        try {
          const parsed = JSON.parse(rawUser);
          user = parsed.username || parsed.user_id || parsed.id || 'me';
        } catch {
          user = rawUser;
        }
      } else {
        user = (await storageGet('username')) || 'me';
      }

      const resp = await fetch(`${serverUrl}/api/geo/steps`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ user_id: user, steps: dailyStepsRef.current, timestamp: Date.now() / 1000 }),
      });
      if (!resp.ok) {
        const body = await resp.text().catch(() => '');
        logSensor('steps', `POST /api/geo/steps failed HTTP ${resp.status}`, body);
        patchSensor('steps', { message: `Step sync failed (HTTP ${resp.status})` });
        return;
      }
      lastSyncedStepsRef.current = dailyStepsRef.current;
      patchSensor('steps', { message: null });
    } catch (err) {
      logSensor('steps', 'sync failed', err);
      patchSensor('steps', { message: errorMessage(err) });
    }
  }, [patchSensor]);

  const syncToGateway = useCallback(async (lat: number, lng: number, accuracy: number | null, speed: number | null) => {
    if (!sensorsRef.current.location.enabled) return;
    try {
      const token = await storageGet('jarvis_api_key');
      const rawServerUrl = await storageGet('jarvis_server_url');
      const serverUrl = rawServerUrl || getServerOrigin();
      if (!token || !serverUrl) {
        logSensor('location', 'breadcrumb skipped: missing token or server URL');
        return;
      }

      let user: string;
      const rawUser = await storageGet('jarvis_user');
      if (rawUser) {
        try {
          const parsed = JSON.parse(rawUser);
          user = parsed.username || parsed.user_id || parsed.id || 'me';
        } catch {
          user = rawUser;
        }
      } else {
        user = (await storageGet('username')) || 'me';
      }

      let battery: number | undefined;
      try {
        if (typeof navigator !== 'undefined' && 'getBattery' in navigator) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const b = await (navigator as any).getBattery();
          if (b && typeof b.level === 'number') {
            battery = Math.round(b.level * 100);
          }
        }
      } catch (err) {
        logSensor('location', 'battery status unavailable', err);
      }

      const payload: Record<string, unknown> = {
        latitude: lat,
        longitude: lng,
        accuracy: accuracy ?? 0,
        speed: speed ?? 0,
        battery,
        timestamp: Date.now() / 1000,
        user_id: user,
      };
      // Attach hardware pedometer reading when present (real sensor data only)
      if (sensorsRef.current.steps.enabled && dailyStepsRef.current !== null) {
        payload.daily_steps = dailyStepsRef.current;
      }

      const resp = await fetch(`${serverUrl}/api/users/${encodeURIComponent(user)}/location`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(payload),
      });

      if (!resp.ok && resp.status === 404) {
        const fallback = await fetch(`${serverUrl}/api/users/location`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify(payload),
        });
        if (!fallback.ok) {
          logSensor('location', `fallback location POST failed HTTP ${fallback.status}`);
          patchSensor('location', { message: `Location sync failed (HTTP ${fallback.status})` });
          return;
        }
      } else if (!resp.ok) {
        const body = await resp.text().catch(() => '');
        logSensor('location', `location POST failed HTTP ${resp.status}`, body);
        patchSensor('location', { message: `Location sync failed (HTTP ${resp.status})` });
        return;
      }
      patchSensor('location', { permission: 'granted', message: null });
    } catch (err) {
      logSensor('location', 'sync failed', err);
      patchSensor('location', { message: errorMessage(err) });
    }
  }, [patchSensor]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleLocationUpdate = useCallback(async (position: any) => {
    if (!sensorsRef.current.location.enabled) return;
    const { latitude, longitude, accuracy, speed } = position.coords;
    const fixTs = typeof position.timestamp === 'number' ? position.timestamp : Date.now();

    // Many Android/iOS fixes omit `speed`. Derive it from consecutive fixes so
    // the server's trip detector (>= 10 mph) actually fires during a drive.
    let speedMps = typeof speed === 'number' && speed > 0 ? speed : 0;
    if (speedMps === 0 && lastFixRef.current) {
      const dtSec = (fixTs - lastFixRef.current.t) / 1000;
      if (dtSec > 0.5 && dtSec < 60) {
        speedMps = calculateDistance(lastFixRef.current.lat, lastFixRef.current.lng, latitude, longitude) / dtSec;
      }
    }
    lastFixRef.current = { lat: latitude, lng: longitude, t: fixTs };
    if (Capacitor.isNativePlatform()) {
      TokenBridge.setLastLocation({ latitude, longitude }).catch((err) =>
        logSensor('location', 'TokenBridge.setLastLocation failed', err)
      );
    }

    const speedMph = speedMps * 2.237;
    const newInterval = speedMph > SPEED_THRESHOLD_MPH ? 'transit' : 'stationary';
    intervalRef.current = newInterval;

    setState((prev) => ({
      ...prev,
      latitude,
      longitude,
      accuracy: accuracy ?? null,
      speed: speedMps,
      timestamp: position.timestamp,
      isTracking: true,
      error: null,
      interval: newInterval,
    }));
    // Successful fix — clear any sticky sensor banner (timeout, old TokenBridge miss, etc.)
    patchSensor('location', { permission: 'granted', message: null });

    if (lastLocationRef.current) {
      const distance = calculateDistance(lastLocationRef.current.lat, lastLocationRef.current.lng, latitude, longitude);
      if (distance < GEOFENCE_RADIUS_M && newInterval === 'stationary') {
        // Stationary under threshold — don't log a breadcrumb, but keep syncing steps
        void syncDailySteps();
        return;
      }
    }

    lastLocationRef.current = { lat: latitude, lng: longitude };
    await syncToGateway(latitude, longitude, accuracy ?? null, speedMps);
  }, [calculateDistance, syncToGateway, syncDailySteps]);

  const startTracking = useCallback(async () => {
    // Guard against re-entry: startTracking identity changes must never stack watches
    if (watchIdRef.current !== null || startingRef.current) return;
    if (!sensorsRef.current.location.enabled) return;
    startingRef.current = true;
    setState((s) => ({ ...s, isTracking: true, error: null }));
    void storageSet('jarvis_location_tracking_enabled', 'true');

    try {
      // Kick off hardware step counting in parallel with location tracking
      if (sensorsRef.current.steps.enabled) {
        void refreshDailySteps();
        try {
          await StepCounter.startPolling();
          if (!stepUpdateListenerRef.current) {
            stepUpdateListenerRef.current = await StepCounter.addListener('stepUpdate', (reading) => {
              if (reading.available && typeof reading.steps === 'number') {
                const changed = dailyStepsRef.current !== reading.steps;
                dailyStepsRef.current = reading.steps;
                // Push new step counts immediately — don't wait for a GPS fix
                if (changed) void syncDailySteps();
              }
            });
          }
        } catch (err) {
          logSensor('steps', 'startPolling failed', err);
          patchSensor('steps', {
            enabled: false,
            permission: 'denied',
            message: errorMessage(err),
          });
          await storageSet(KEY_STEPS_ENABLED, 'false');
        }
      }

      if (!Capacitor.isNativePlatform()) {
        if (!navigator.geolocation) {
          const msg = 'Geolocation is not supported by this browser';
          setState((s) => ({ ...s, error: msg, isTracking: false }));
          patchSensor('location', { enabled: false, permission: 'unavailable', message: msg });
          await storageSet(KEY_LOCATION_ENABLED, 'false');
          return;
        }

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const success = (pos: any) => {
          handleLocationUpdate(pos);
        };

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const error = (err: any) => {
          logSensor('location', 'watchPosition error', err);
          setState((s) => ({ ...s, error: err.message, isTracking: false }));
          if (err?.code === 1) {
            patchSensor('location', { enabled: false, permission: 'denied', message: err.message });
            void storageSet(KEY_LOCATION_ENABLED, 'false');
            toast.error('Location permission denied — tracking turned off');
          } else {
            patchSensor('location', { message: err.message });
          }
        };

        const options = { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 };

        navigator.geolocation.getCurrentPosition(success, error, options);
        const watchId = navigator.geolocation.watchPosition(success, error, options);
        watchIdRef.current = watchId;
        return;
      }

      try {
        const permission = await Geolocation.checkPermissions();
        if (permission.location === 'denied') {
          const request = await Geolocation.requestPermissions();
          if (request.location === 'denied') {
            const msg = 'Location permission denied';
            setState((s) => ({ ...s, error: msg, isTracking: false }));
            patchSensor('location', { enabled: false, permission: 'denied', message: msg });
            await storageSet(KEY_LOCATION_ENABLED, 'false');
            toast.error('Location tracking disabled — permission denied');
            return;
          }
        }
        patchSensor('location', { permission: 'granted', message: null });

        // Start the continuous watch FIRST so a slow first fix never blocks
        // tracking. A one-shot getCurrentPosition timeout used to abort here
        // and surface "Could not obtain location in time" with no watch running.
        const gpsOptions = { enableHighAccuracy: true, timeout: 30000, maximumAge: 5000 };
        watchIdRef.current = await Geolocation.watchPosition(
          gpsOptions,
          (pos, err) => {
            if (pos) {
              setState((s) => ({ ...s, error: null }));
              handleLocationUpdate(pos);
            }
            if (err) {
              logSensor('location', 'native watch error', err);
              // Transient timeout while acquiring a fix is not a hard failure —
              // only surface permission-class errors as blocking errors.
              if (err.code === 1) {
                setState((s) => ({ ...s, error: err.message, isTracking: false }));
                patchSensor('location', { enabled: false, permission: 'denied', message: err.message });
                void storageSet(KEY_LOCATION_ENABLED, 'false');
                toast.error('Location permission denied — tracking turned off');
              } else if (err.code === 3) {
                // POSITION_UNAVAILABLE / TIMEOUT — keep watching, soft message only
                patchSensor('location', { message: 'Waiting for GPS fix…' });
                logSensor('location', 'waiting for GPS fix', err.message);
              } else {
                setState((s) => ({ ...s, error: err.message }));
                patchSensor('location', { message: err.message });
              }
            }
          }
        );

        // Opportunistic first fix — failures are non-fatal; the watch is live.
        try {
          const position = await Geolocation.getCurrentPosition(gpsOptions);
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          await handleLocationUpdate(position as any);
          setState((s) => ({ ...s, error: null }));
        } catch (firstFixErr) {
          logSensor('location', 'first fix not ready yet (watch continues)', firstFixErr);
        }

        // Sync steps immediately on tracking start
        void syncDailySteps();

        // Periodic step sync when stationary (treadmill etc.) — reads intervalRef,
        // not state.interval, so the callback never goes stale
        if (stationarySyncTimerRef.current === null) {
          stationarySyncTimerRef.current = window.setInterval(() => {
            if (intervalRef.current === 'stationary') {
              void syncDailySteps();
            }
          }, DAILY_STEPS_SYNC_INTERVAL_MS);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to start location tracking';
        logSensor('location', 'startTracking failed', err);
        setState((s) => ({ ...s, error: msg, isTracking: false }));
        if (msg.toLowerCase().includes('permission') || msg.toLowerCase().includes('denied')) {
          patchSensor('location', { enabled: false, permission: 'denied', message: msg });
          await storageSet(KEY_LOCATION_ENABLED, 'false');
          toast.error('Location tracking disabled — permission denied');
        } else {
          patchSensor('location', { message: msg });
        }
      }
    } finally {
      startingRef.current = false;
    }
  }, [handleLocationUpdate, refreshDailySteps, syncDailySteps, patchSensor]);

  const stopTracking = useCallback(() => {
    void storageSet('jarvis_location_tracking_enabled', 'false');
    if (watchIdRef.current !== null) {
      if (typeof watchIdRef.current === 'number') {
        navigator.geolocation.clearWatch(watchIdRef.current);
      } else {
        Geolocation.clearWatch({ id: watchIdRef.current });
      }
      watchIdRef.current = null;
    }
    if (stationarySyncTimerRef.current !== null) {
      clearInterval(stationarySyncTimerRef.current);
      stationarySyncTimerRef.current = null;
    }
    setState((s) => ({ ...s, isTracking: false }));
  }, []);

  const enableSensor = useCallback(async (id: SensorId): Promise<boolean> => {
    if (id === 'steps') {
      patchSensor('steps', { enabled: true, message: null });
      await storageSet(KEY_STEPS_ENABLED, 'true');
      try {
        const avail = await StepCounter.isAvailable();
        if (!avail.available) {
          patchSensor('steps', {
            enabled: false,
            permission: 'unavailable',
            message: 'No hardware step counter on this device',
          });
          await storageSet(KEY_STEPS_ENABLED, 'false');
          toast.error('Step counter unavailable on this device');
          return false;
        }
        if (avail.permissionRequired && !avail.permissionGranted) {
          const req = await StepCounter.requestPermission();
          if (!req.granted) {
            patchSensor('steps', {
              enabled: false,
              permission: 'denied',
              message: req.permanentlyDenied
                ? 'Blocked in system settings — open Settings to allow'
                : 'Permission denied — steps stay off',
            });
            await storageSet(KEY_STEPS_ENABLED, 'false');
            toast.error('Step permission denied');
            if (req.permanentlyDenied) {
              try {
                await StepCounter.openSettings();
              } catch (err) {
                logSensor('steps', 'openSettings failed', err);
              }
            }
            return false;
          }
        }
        patchSensor('steps', { enabled: true, permission: 'granted', message: null });
        stepsPluginReadyRef.current = false;
        void refreshDailySteps().then(() => syncDailySteps());
        try {
          await StepCounter.startPolling();
          if (!stepUpdateListenerRef.current) {
            stepUpdateListenerRef.current = await StepCounter.addListener('stepUpdate', (reading) => {
              if (reading.available && typeof reading.steps === 'number') {
                const changed = dailyStepsRef.current !== reading.steps;
                dailyStepsRef.current = reading.steps;
                if (changed) void syncDailySteps();
              }
            });
          }
        } catch (err) {
          logSensor('steps', 'startPolling failed on enable', err);
          patchSensor('steps', { message: errorMessage(err) });
        }
        return true;
      } catch (err) {
        logSensor('steps', 'enable failed', err);
        patchSensor('steps', { enabled: false, message: errorMessage(err) });
        await storageSet(KEY_STEPS_ENABLED, 'false');
        toast.error(`Could not enable steps: ${errorMessage(err)}`);
        return false;
      }
    }

    // Location
    patchSensor('location', { enabled: true, permission: 'unknown', message: null });
    await storageSet(KEY_LOCATION_ENABLED, 'true');
    await storageSet('jarvis_location_tracking_enabled', 'true');
    await startTracking();
    return watchIdRef.current !== null || sensorsRef.current.location.enabled === true;
  }, [patchSensor, refreshDailySteps, syncDailySteps, startTracking]);

  const disableSensor = useCallback(async (id: SensorId): Promise<void> => {
    if (id === 'steps') {
      patchSensor('steps', { enabled: false, message: null });
      await storageSet(KEY_STEPS_ENABLED, 'false');
      await storageSet('jarvis_steps_enabled', 'false');
      await stopStepService();
      logSensor('steps', 'sensor disabled by user');
      return;
    }
    patchSensor('location', { enabled: false, message: null });
    await storageSet(KEY_LOCATION_ENABLED, 'false');
    await storageSet('jarvis_location_tracking_enabled', 'false');
    stopTracking();
    lastLocationRef.current = null;
    logSensor('location', 'sensor disabled by user');
  }, [patchSensor, stopStepService, stopTracking]);

  const openSensorSettings = useCallback(async (id: SensorId) => {
    if (id !== 'steps') {
      toast('Open system Settings → Apps → Jarvis OS → Permissions', { icon: 'ℹ️' });
      return;
    }
    try {
      await StepCounter.openSettings();
    } catch (err) {
      logSensor('steps', 'openSettings failed', err);
      toast.error('Could not open system settings');
    }
  }, []);

  // Load persisted sensor prefs and start any that were left on
  useEffect(() => {
    let cancelled = false;
    async function initSensors() {
      try {
        const [locPref, stepsPref, legacyLoc] = await Promise.all([
          storageGet(KEY_LOCATION_ENABLED),
          storageGet(KEY_STEPS_ENABLED),
          storageGet('jarvis_location_tracking_enabled'),
        ]);
        if (cancelled) return;

        // Default location ON for native (first run / legacy installs), OFF if explicitly off
        const locationOn = locPref !== null
          ? locPref === 'true'
          : (legacyLoc !== null ? legacyLoc === 'true' : Capacitor.isNativePlatform());
        // Default steps ON so first run can prompt; denial will flip it off
        const stepsOn = stepsPref !== 'true' ? stepsPref !== 'false' : true;

        setSensors(() => {
          const next = {
            location: {
              enabled: locationOn,
              permission: locationOn ? 'unknown' : 'disabled',
              message: null,
            },
            steps: {
              enabled: stepsOn,
              permission: stepsOn ? 'unknown' : 'disabled',
              message: null,
            },
          };
          sensorsRef.current = next;
          return next;
        });

        if (locationOn) {
          void startTracking();
        }
        if (stepsOn) {
          void refreshDailySteps().then(() => syncDailySteps());
          try {
            await StepCounter.startPolling();
            if (!stepUpdateListenerRef.current) {
              stepUpdateListenerRef.current = await StepCounter.addListener('stepUpdate', (reading) => {
                if (reading.available && typeof reading.steps === 'number') {
                  const changed = dailyStepsRef.current !== reading.steps;
                  dailyStepsRef.current = reading.steps;
                  if (changed) void syncDailySteps();
                }
              });
            }
          } catch (err) {
            logSensor('steps', 'polling start failed on init', err);
            // Web / no sensor — leave feature state as-is; permission path reports unavailable
            if (!Capacitor.isNativePlatform()) {
              patchSensor('steps', { permission: 'unavailable', message: 'Step counter requires the native app' });
            }
          }
          if (stationarySyncTimerRef.current === null) {
            stationarySyncTimerRef.current = window.setInterval(() => {
              void refreshDailySteps().then(() => syncDailySteps());
            }, DAILY_STEPS_SYNC_INTERVAL_MS);
          }
        }
      } catch (err) {
        logSensor('init', 'failed to load sensor preferences', err);
      }
    }
    void initSensors();
    return () => {
      cancelled = true;
    };
  }, [startTracking, refreshDailySteps, syncDailySteps, patchSensor]);

  // Midnight rollover: re-read the sensor so the new day's bucket starts even
  // if no step event has fired yet after 00:00 local time.
  useEffect(() => {
    const checkMidnight = () => {
      const d = new Date();
      if (d.getHours() === 0 && d.getMinutes() === 0) {
        void refreshDailySteps().then(() => syncDailySteps());
      }
    };
    const midnightTimer = window.setInterval(checkMidnight, 30000);
    return () => window.clearInterval(midnightTimer);
  }, [refreshDailySteps, syncDailySteps]);

  useEffect(() => {
    return () => {
      if (watchIdRef.current !== null) {
        if (typeof watchIdRef.current === 'number') {
          navigator.geolocation.clearWatch(watchIdRef.current);
        } else {
          Geolocation.clearWatch({ id: watchIdRef.current });
        }
      }
      void StepCounter.stopPolling();
      if (stepUpdateListenerRef.current) {
        void stepUpdateListenerRef.current.remove();
        stepUpdateListenerRef.current = null;
      }
      if (stationarySyncTimerRef.current !== null) {
        clearInterval(stationarySyncTimerRef.current);
        stationarySyncTimerRef.current = null;
      }
    };
  }, []);

  // Refresh hardware step count when app returns to foreground — and push it,
  // otherwise the server only sees steps after the next GPS fix
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        if (sensorsRef.current.steps.enabled) {
          void refreshDailySteps().then(() => syncDailySteps());
        }
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refreshDailySteps, syncDailySteps]);

  const contextValue = useMemo(() => ({
    ...state,
    sensors,
    enableSensor,
    disableSensor,
    openSensorSettings,
    startTracking,
    stopTracking,
  }), [state, sensors, enableSensor, disableSensor, openSensorSettings, startTracking, stopTracking]);

  return (
    <LocationContext.Provider value={contextValue}>
      {children}
    </LocationContext.Provider>
  );
}

export function useLocation() {
  const ctx = useContext(LocationContext);
  if (!ctx) throw new Error('useLocation must be used within a LocationProvider');
  return ctx;
}

export function useBackgroundLocation() {
  return useLocation();
}
/* eslint-enable react-refresh/only-export-components */
