import { useCallback, useEffect, useRef, useState } from 'react';
import {
  connectWatch,
  disconnectWatch,
  isWatchBleSupported,
  isWatchConnected,
  notifyWatch,
  onWatchDisconnect,
  readWatchStats,
  type WatchStats,
} from '../lib/watchBle';
import { askWatchOrQueue, deliverWatchAnswers, type WatchAskResult } from '../lib/watchOffline';
import OwnSync from '../plugins/ownSync';

/** How often the panel re-reads the snapshot while the watch is connected. */
const REFRESH_MS = 30000;

/**
 * A snapshot this phone read becomes one of the person's own items (My
 * devices), so their other devices see the watch's steps and battery too, and
 * any watch question queued offline that has its answer now goes to the watch.
 * Neither may disturb the panel.
 */
function afterRead(stats: WatchStats | null): void {
  if (stats) void OwnSync.noteWatch({ stats: { ...stats } }).catch(() => undefined);
  void deliverWatchAnswers().catch(() => 0);
}

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
  /** Ask Jarvis through the watch; queued offline when the watch cannot reach Jarvis. */
  ask: (question: string) => Promise<WatchAskResult>;
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
    afterRead(next);
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
      const next = ok ? await readWatchStats() : null;
      afterRead(next);
      setStats(next);
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

  const ask = useCallback((question: string) => askWatchOrQueue(question), []);

  // The plugin's own drop callback is the only timely signal that the watch
  // went away; without it the panel would keep claiming "Connected" until the
  // next 30 s poll happened to fail.
  useEffect(() => {
    return onWatchDisconnect(() => {
      if (!mounted.current) return;
      setConnected(false);
      setStats(null);
    });
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

  return { supported, connected, stats, connect, disconnect, refresh, sendNotification, ask };
}
