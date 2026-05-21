import { createContext, useContext } from 'react';

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

export const LocationContext = createContext<LocationContextValue | null>(null);

export function useLocation() {
  const ctx = useContext(LocationContext);
  if (!ctx) throw new Error('useLocation must be used within a LocationProvider');
  return ctx;
}

export function useBackgroundLocation() {
  return useLocation();
}
