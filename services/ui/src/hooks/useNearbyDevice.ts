import { useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import Nearby, { type NearbyDeviceState } from '../plugins/nearby';

/** This Android phone's Nearby & offline parts (null in a browser or before the first read). */
export function useNearbyDevice(): NearbyDeviceState | null {
  const [state, setState] = useState<NearbyDeviceState | null>(null);
  useEffect(() => {
    if (Capacitor.getPlatform() !== 'android') return undefined;
    let alive = true;
    const read = () => {
      Nearby.getState().then((s) => alive && setState(s)).catch(() => alive && setState(null));
    };
    read();
    document.addEventListener('visibilitychange', read);
    return () => {
      alive = false;
      document.removeEventListener('visibilitychange', read);
    };
  }, []);
  return state;
}
