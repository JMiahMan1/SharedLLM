import { useEffect } from 'react';
import { Capacitor } from '@capacitor/core';
import { useAuth } from '../context/AuthContext';
import { api } from '../services/api';
import Nearby from '../plugins/nearby';

/**
 * Settings > Nearby & offline reaches the phone each time the app opens (or
 * someone signs in): the parts stored on the server are applied natively,
 * with no prompt for a grown member (their own switch asks, when turned on).
 * A child's phone asks for Nearby devices if it lacks it: the parent link is
 * always on there. Renders nothing.
 */
export default function NearbySync() {
  const { token, user } = useAuth();
  const username = user?.username;
  useEffect(() => {
    if (Capacitor.getPlatform() !== 'android' || !token || !username) return;
    let cancelled = false;
    api
      .getNearbySettings()
      .then(async (state) => {
        if (cancelled) return;
        const device = await Nearby.apply({ ...state.settings, ask: false });
        if (state.managed_by_parent && (!device.scanPermission || !device.connectPermission)) {
          await Nearby.requestPermissions();
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token, username]);
  return null;
}
