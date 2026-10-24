import { useEffect } from 'react';
import { Capacitor } from '@capacitor/core';
import { checkGuard } from '../../lib/guardWatch';
import { useAuth } from '../../context/AuthContext';

const CHECK_MS = 5 * 60 * 1000;

export default function GuardWatch() {
  const username = useAuth().user?.username;
  useEffect(() => {
    if (!username || Capacitor.getPlatform() !== 'android') return undefined;
    const tick = () => void checkGuard(username).catch(() => undefined);
    tick();
    const timer = window.setInterval(tick, CHECK_MS);
    return () => window.clearInterval(timer);
  }, [username]);
  return null;
}
