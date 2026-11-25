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
        if (!state.chosen && !state.managed_by_parent) {
          // Nothing saved yet: keep what this phone already runs (its Wander switches before
          // Settings > Nearby & offline existed) and save it, instead of switching it off with
          // the defaults. Updating to the build with the new settings turned a parent's Find
          // family nearby off this way (2026-11-25).
          const device = await Nearby.getState();
          await api.setNearbySettings({ nearby: device.nearby, chat: device.chat, offline: device.offline, car: device.car });
          return;
        }
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
