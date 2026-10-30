import { useCallback, useEffect, useRef, useState } from 'react';
import {
  connectWatch,
  disconnectWatch,
  isWatchBleSupported,
  isWatchConnected,
  notifyWatch,
  readWatchStats,
  type WatchStats,
} from '../lib/watchBle';

/** How often the panel re-reads the snapshot while the watch is connected. */
const REFRESH_MS = 30000;

export interface UseWatchBleResult {
  /** Whether this build has a Bluetooth radio to talk over (false on web). */
  supported: boolean;
  connected: boolean;
  stats: WatchStats | null;
  connect: () => Promise<boolean>;
  disconnect: () => Promise<void>;
  /** Re-read the snapshot on demand (the panel's Refresh). */
  refresh: () => Promise<void>;
  sendNotification: (title: string, text: string) => Promise<void>;
}

/**
 * React state around the watch BLE wrapper.
 *
 * The wrapper is deliberately quiet (reads return null, commands log), so the
 * hook is where the panel's truth lives: it owns whether we are connected and
 * the latest snapshot, and it polls only while connected so an idle page does
 * no BLE work.
 */
export function useWatchBle(): UseWatchBleResult {
  const [supported] = useState(() => isWatchBleSupported());
  const [connected, setConnected] = useState(() => isWatchConnected());
  const [stats, setStats] = useState<WatchStats | null>(null);

  // A pending await must not set state after the panel is gone.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!isWatchConnected()) {
      if (mounted.current) {
        setConnected(false);
        setStats(null);
      }
      return;
    }
    const next = await readWatchStats();
    if (!mounted.current) return;
    setStats(next);
    // A null read on a link that has since dropped is how a disconnect that
    // did not fire the plugin's callback still reaches the UI.
    if (!next) setConnected(isWatchConnected());
  }, []);

  const connect = useCallback(async (): Promise<boolean> => {
    const ok = await connectWatch();
    if (mounted.current) {
      setConnected(ok);
      setStats(ok ? await readWatchStats() : null);
    }
    return ok;
  }, []);

  const disconnect = useCallback(async () => {
    await disconnectWatch();
    if (!mounted.current) return;
    setConnected(false);
    setStats(null);
  }, []);

  const sendNotification = useCallback(async (title: string, text: string) => {
    await notifyWatch(title, text);
  }, []);

  useEffect(() => {
    if (!connected) return;
    // Read once on connect, then poll. `refresh` is async, so the state it sets
    // lands after this effect has returned; the rule cannot see across the await.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async fetch, not a synchronous render loop
    void refresh();
    const timer = setInterval(() => {
      void refresh();
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [connected, refresh]);

  return { supported, connected, stats, connect, disconnect, refresh, sendNotification };
}
