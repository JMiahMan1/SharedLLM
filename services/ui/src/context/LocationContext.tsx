import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
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

const LocationContext = createContext<LocationContextValue | null>(null);

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
  const watchIdRef = useRef<number | null>(null);

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
      if (!token) return;
      await fetch('/api/identity/users/location', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ latitude: lat, longitude: lng, accuracy, speed, timestamp: Date.now() }),
      });
    } catch {
      // Will retry on next update
    }
  }, []);

  const handleLocationUpdate = useCallback(async (position: GeolocationPosition) => {
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
    if (!Capacitor.isNativePlatform()) {
      setState((s) => ({ ...s, error: 'Location tracking requires native platform' }));
      return;
    }

    try {
      const permission = await Geolocation.checkPermissions();
      if (permission.location === 'denied') {
        const request = await Geolocation.requestPermissions();
        if (request.location === 'denied') {
          setState((s) => ({ ...s, error: 'Location permission denied' }));
          return;
        }
      }

      const position = await Geolocation.getCurrentPosition({ enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
      await handleLocationUpdate(position);

      watchIdRef.current = await Geolocation.watchPosition(
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
        (pos, err) => {
          if (pos) handleLocationUpdate(pos);
          if (err) setState((s) => ({ ...s, error: err.message }));
        }
      );
    } catch (err) {
      setState((s) => ({ ...s, error: err instanceof Error ? err.message : 'Failed to start location tracking' }));
    }
  }, [handleLocationUpdate]);

  const stopTracking = useCallback(() => {
    if (watchIdRef.current !== null) {
      Geolocation.clearWatch({ id: watchIdRef.current });
      watchIdRef.current = null;
    }
    setState((s) => ({ ...s, isTracking: false }));
  }, []);

  useEffect(() => {
    return () => {
      if (watchIdRef.current !== null) {
        Geolocation.clearWatch({ id: watchIdRef.current });
      }
    };
  }, []);

  return (
    <LocationContext.Provider value={{ ...state, startTracking, stopTracking }}>
      {children}
    </LocationContext.Provider>
  );
}

export { useLocation, useBackgroundLocation } from './LocationContext.types';
