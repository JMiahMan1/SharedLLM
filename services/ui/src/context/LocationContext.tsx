import { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Capacitor } from '@capacitor/core';
import { Geolocation } from '@capacitor/geolocation';
import { storageGet, storageSet } from '../lib/storage';
import { getServerOrigin } from '../lib/serverUrl';
import StepCounter from '../plugins/stepCounter';

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

interface LocationContextValue extends LocationState {
  startTracking: () => Promise<void>;
  stopTracking: () => void;
}

/* eslint-disable react-refresh/only-export-components */
export const LocationContext = createContext<LocationContextValue | null>(null);

const SPEED_THRESHOLD_MPH = 15;
const GEOFENCE_RADIUS_M = 200; // ~1/8 mile — don't log routes under this when stationary
const DAILY_STEPS_SYNC_INTERVAL_MS = 30000; // sync steps at least every 30s when stationary

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

  const calculateDistance = useCallback((lat1: number, lng1: number, lat2: number, lng2: number) => {
    const R = 6371e3;
    const φ1 = (lat1 * Math.PI) / 180;
    const φ2 = (lat2 * Math.PI) / 180;
    const Δφ = ((lat2 - lat1) * Math.PI) / 180;
    const Δλ = ((lng2 - lng1) * Math.PI) / 180;
    const a = Math.sin(Δφ / 2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }, []);

  const refreshDailySteps = useCallback(async () => {
    try {
      if (!stepsPluginReadyRef.current) {
        const avail = await StepCounter.isAvailable();
        if (!avail.available) return;
        if (avail.permissionRequired && !avail.permissionGranted) {
          const req = await StepCounter.requestPermission();
          if (!req.granted) return;
        }
        stepsPluginReadyRef.current = true;
      }
      const reading = await StepCounter.getTodaySteps();
      if (reading.available && typeof reading.steps === 'number' && reading.steps >= 0) {
        dailyStepsRef.current = reading.steps;
      }
    } catch {
      // Sensor or permission unavailable — leave null, never fake a value
    }
  }, []);

  // Sync steps independently of location updates (treadmill/stationary use)
  const syncDailySteps = useCallback(async () => {
    try {
      const token = await storageGet('jarvis_api_key');
      const rawServerUrl = await storageGet('jarvis_server_url');
      const serverUrl = rawServerUrl || getServerOrigin();
      if (!token || !serverUrl) return;
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

      await fetch(`${serverUrl}/api/geo/steps`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ user_id: user, steps: dailyStepsRef.current, timestamp: Date.now() / 1000 }),
      });
      lastSyncedStepsRef.current = dailyStepsRef.current;
    } catch {
      // Will retry next time
    }
  }, []);

  const syncToGateway = useCallback(async (lat: number, lng: number, accuracy: number | null, speed: number | null) => {
    try {
      const token = await storageGet('jarvis_api_key');
      const rawServerUrl = await storageGet('jarvis_server_url');
      const serverUrl = rawServerUrl || getServerOrigin();
      if (!token || !serverUrl) return;

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
      } catch {
        // Battery status unavailable
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
      if (dailyStepsRef.current !== null) {
        payload.daily_steps = dailyStepsRef.current;
      }

      const resp = await fetch(`${serverUrl}/api/users/${encodeURIComponent(user)}/location`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(payload),
      });

      if (!resp.ok && resp.status === 404) {
        await fetch(`${serverUrl}/api/users/location`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify(payload),
        });
      }
    } catch {
      // Will retry on next update
    }
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleLocationUpdate = useCallback(async (position: any) => {
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
    startingRef.current = true;
    setState((s) => ({ ...s, isTracking: true, error: null }));
    void storageSet('jarvis_location_tracking_enabled', 'true');

    try {
      // Kick off hardware step counting in parallel with location tracking
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
      } catch {
        // Web platform or sensor absent — GPS stride model remains the fallback
      }

      if (!Capacitor.isNativePlatform()) {
        if (!navigator.geolocation) {
          setState((s) => ({ ...s, error: 'Geolocation is not supported by this browser', isTracking: false }));
          return;
        }

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const success = (pos: any) => {
          handleLocationUpdate(pos);
        };

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const error = (err: any) => {
          setState((s) => ({ ...s, error: err.message, isTracking: false }));
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
            setState((s) => ({ ...s, error: 'Location permission denied', isTracking: false }));
            return;
          }
        }

        const position = await Geolocation.getCurrentPosition({ enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        await handleLocationUpdate(position as any);

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

        watchIdRef.current = await Geolocation.watchPosition(
          { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
          (pos, err) => {
            if (pos) handleLocationUpdate(pos);
            if (err) setState((s) => ({ ...s, error: err.message }));
          }
        );
      } catch (err) {
        setState((s) => ({ ...s, error: err instanceof Error ? err.message : 'Failed to start location tracking', isTracking: false }));
      }
    } finally {
      startingRef.current = false;
    }
  }, [handleLocationUpdate, refreshDailySteps, syncDailySteps]);

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
    void StepCounter.stopPolling();
    setState((s) => ({ ...s, isTracking: false }));
  }, []);

  // Auto-resume tracking if previously enabled or if running on mobile
  useEffect(() => {
    async function initTracking() {
      const saved = await storageGet('jarvis_location_tracking_enabled');
      const shouldTrack = saved === 'true' || (saved === null && Capacitor.isNativePlatform());
      if (shouldTrack) {
        void startTracking();
      }
    }
    void initTracking();
  }, [startTracking]);

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
    };
  }, []);

  // Refresh hardware step count when app returns to foreground — and push it,
  // otherwise the server only sees steps after the next GPS fix
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void refreshDailySteps().then(() => syncDailySteps());
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refreshDailySteps, syncDailySteps]);

  const contextValue = useMemo(() => ({
    ...state,
    startTracking,
    stopTracking
  }), [state, startTracking, stopTracking]);

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
