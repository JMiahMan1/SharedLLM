import { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Capacitor } from '@capacitor/core';
import { Geolocation } from '@capacitor/geolocation';
import { storageGet } from '../lib/storage';

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
const GEOFENCE_RADIUS_M = 100;

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
  const watchIdRef = useRef<string | number | null>(null);

  const calculateDistance = useCallback((lat1: number, lng1: number, lat2: number, lng2: number) => {
    const R = 6371e3;
    const φ1 = (lat1 * Math.PI) / 180;
    const φ2 = (lat2 * Math.PI) / 180;
    const Δφ = ((lat2 - lat1) * Math.PI) / 180;
    const Δλ = ((lng2 - lng1) * Math.PI) / 180;
    const a = Math.sin(Δφ / 2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }, []);

  const syncToGateway = useCallback(async (lat: number, lng: number, accuracy: number | null, speed: number | null) => {
    try {
      const token = await storageGet('jarvis_api_key');
      const serverUrl = await storageGet('jarvis_server_url');
      if (!token || !serverUrl) return;
      await fetch(`${serverUrl}/api/identity/users/location`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ latitude: lat, longitude: lng, accuracy, speed, timestamp: Date.now() }),
      });
    } catch {
      // Will retry on next update
    }
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleLocationUpdate = useCallback(async (position: any) => {
    const { latitude, longitude, accuracy, speed } = position.coords;
    const speedMph = speed ? speed * 2.237 : 0;
    const newInterval = speedMph > SPEED_THRESHOLD_MPH ? 'transit' : 'stationary';

    setState((prev) => ({
      ...prev,
      latitude,
      longitude,
      accuracy: accuracy ?? null,
      speed: speed ?? null,
      timestamp: position.timestamp,
      isTracking: true,
      error: null,
      interval: newInterval,
    }));

    if (lastLocationRef.current) {
      const distance = calculateDistance(lastLocationRef.current.lat, lastLocationRef.current.lng, latitude, longitude);
      if (distance < GEOFENCE_RADIUS_M && newInterval === 'stationary') return;
    }

    lastLocationRef.current = { lat: latitude, lng: longitude };
    await syncToGateway(latitude, longitude, accuracy ?? null, speed ?? null);
  }, [calculateDistance, syncToGateway]);

  const startTracking = useCallback(async () => {
    setState((s) => ({ ...s, isTracking: true, error: null }));

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
  }, [handleLocationUpdate]);

  const stopTracking = useCallback(() => {
    if (watchIdRef.current !== null) {
      if (typeof watchIdRef.current === 'number') {
        navigator.geolocation.clearWatch(watchIdRef.current);
      } else {
        Geolocation.clearWatch({ id: watchIdRef.current });
      }
      watchIdRef.current = null;
    }
    setState((s) => ({ ...s, isTracking: false }));
  }, []);

  useEffect(() => {
    return () => {
      if (watchIdRef.current !== null) {
        if (typeof watchIdRef.current === 'number') {
          navigator.geolocation.clearWatch(watchIdRef.current);
        } else {
          Geolocation.clearWatch({ id: watchIdRef.current });
        }
      }
    };
  }, []);

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
