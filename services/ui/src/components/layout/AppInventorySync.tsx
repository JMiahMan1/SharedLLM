import { useEffect } from 'react';
import { Capacitor } from '@capacitor/core';

import { useAuth } from '../../context/AuthContext';
import { syncAppInventory } from '../../lib/appInventorySync';

const CHECK_INTERVAL_MS = 5 * 60 * 1000;

/**
 * App vetting (docs/APP_VETTING.md): a child's or teen's Android device
 * reports the apps installed on it, so their parents see each one's rating
 * and review. Never from an adult's device: nothing is read or sent there
 * (and the server refuses it anyway).
 */
export default function AppInventorySync() {
  const { user } = useAuth();
  const minor = Boolean(user?.is_minor);
  const username = user?.username || '';
  useEffect(() => {
    if (!minor || !username || Capacitor.getPlatform() !== 'android') return undefined;
    void syncAppInventory(username);
    const timer = window.setInterval(() => void syncAppInventory(username), CHECK_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [minor, username]);
  return null;
}
