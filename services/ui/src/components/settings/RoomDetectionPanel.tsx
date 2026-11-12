import { useCallback, useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { Bluetooth } from 'lucide-react';
import BleBeacon, { type BeaconStatus, type BeaconTxPower } from '../../plugins/bleBeacon';

const POWERS: { level: BeaconTxPower; label: string }[] = [
  { level: 'ultra_low', label: 'Lowest' },
  { level: 'low', label: 'Low' },
  { level: 'medium', label: 'Medium' },
  { level: 'high', label: 'High' },
];

/** One line saying what the beacon is doing. Never quiet about a failure. */
export function beaconStateLine(s: BeaconStatus): string {
  switch (s.state) {
    case 'advertising':
      return 'On: the house can tell which room this phone is in.';
    case 'starting':
      return 'Starting...';
    case 'bluetooth_off':
      return 'Waiting for Bluetooth: it starts again when Bluetooth is back on.';
    case 'disabled':
      return 'Off: the house does not know which room this phone is in.';
    case 'unavailable':
      return `Room detection unavailable: ${s.reason || 'the phone did not say why'}`;
    default:
      return `Room detection unavailable: unknown state ${String(s.state)}`;
  }
}

/**
 * Room detection (Android only): this phone sends a small Bluetooth signal
 * the house's listeners hear, so Jarvis knows which room it is in (for "where
 * is...", announcements to the room someone is in). On a child's phone it
 * stays on; a parent manages it.
 */
export default function RoomDetectionPanel() {
  const android = Capacitor.getPlatform() === 'android';
  const [status, setStatus] = useState<BeaconStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const read = useCallback(() => {
    BleBeacon.getStatus()
      .then((s) => {
        setStatus(s);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!android) return undefined;
    read();
    // Coming back from Android's settings (the permission page): read it again.
    document.addEventListener('visibilitychange', read);
    let handle: { remove: () => Promise<void> } | null = null;
    let alive = true;
    void BleBeacon.addListener('statusChange', (s) => alive && setStatus(s))
      .then((h) => {
        if (alive) handle = h;
        else void h.remove();
      })
      .catch(() => undefined);
    return () => {
      alive = false;
      document.removeEventListener('visibilitychange', read);
      if (handle) void handle.remove();
    };
  }, [android, read]);

  if (!android) return null;

  const run = (fn: () => Promise<BeaconStatus>) => {
    setBusy(true);
    fn()
      .then((s) => {
        setStatus(s);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  const locked = status?.canDisable === false;
  const permissionMissing = status != null && !status.permissionGranted;

  return (
    <section className="glass-panel rounded-2xl p-4 space-y-3" aria-labelledby="room-detection-heading" data-testid="room-detection-panel">
      <div className="px-1">
        <h2 id="room-detection-heading" className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
          <Bluetooth size={16} className="text-purple-400" />
          Room detection
        </h2>
        <p className="text-xs text-slate-500 mt-1">
          This phone sends a small, low-power Bluetooth signal that the house's listeners hear, so Jarvis knows which room
          you are in: "where is" answers and announcements to the room someone is in. It carries a random id, not your
          name. Android asks once for the Nearby devices permission, which is what lets an app send that signal.
        </p>
      </div>

      {error && !status && (
        <p className="text-sm text-amber-300" role="alert" data-testid="room-detection-error">
          Room detection unavailable: {error}
        </p>
      )}

      {status && (
        <>
          <div className="flex items-center justify-between gap-3 rounded-xl bg-white/5 p-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-white">Detect my room</p>
              <p
                className={`text-xs ${status.state === 'unavailable' ? 'text-amber-300' : 'text-slate-400'}`}
                data-testid="room-detection-state"
                role={status.state === 'unavailable' ? 'alert' : undefined}
              >
                {beaconStateLine(status)}
              </p>
              {locked && (
                <p className="text-xs text-slate-500 mt-1" data-testid="room-detection-locked">
                  On a child's phone room detection stays on. A parent manages it.
                </p>
              )}
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={status.enabled}
              aria-label="Detect my room"
              disabled={busy || (locked && status.enabled)}
              onClick={() => run(() => BleBeacon.setEnabled({ enabled: !status.enabled }))}
              className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
                status.enabled ? 'bg-emerald-500' : 'bg-white/10'
              }`}
              data-testid="room-detection-toggle"
            >
              <span
                className={`inline-block h-5 w-5 rounded-full bg-white transition-transform ${status.enabled ? 'translate-x-6' : 'translate-x-1'}`}
              />
            </button>
          </div>

          {status.enabled && permissionMissing && (
            <div className="flex flex-wrap gap-2" data-testid="room-detection-permission">
              <button
                type="button"
                onClick={() => run(() => BleBeacon.requestPermission())}
                disabled={busy}
                className="glass-button min-h-11 rounded-xl px-3 text-xs font-semibold disabled:opacity-50"
              >
                Allow Nearby devices
              </button>
              <button
                type="button"
                onClick={() => void BleBeacon.openSettings().then(read)}
                className="min-h-11 rounded-xl bg-white/5 px-3 text-xs text-slate-200 hover:bg-white/10"
              >
                Open settings
              </button>
            </div>
          )}

          {status.enabled && (
            <fieldset className="space-y-1">
              <legend className="px-1 text-xs text-slate-400">Signal strength (higher reaches further and uses a little more battery)</legend>
              <div className="flex flex-wrap gap-2" data-testid="room-detection-power">
                {POWERS.map((p) => (
                  <button
                    key={p.level}
                    type="button"
                    aria-pressed={status.txPowerLevel === p.level}
                    disabled={busy}
                    onClick={() => run(() => BleBeacon.setTxPower({ level: p.level }))}
                    className={`min-h-11 rounded-xl px-3 text-sm disabled:opacity-50 ${
                      status.txPowerLevel === p.level ? 'bg-purple-500/30 text-white' : 'bg-white/5 text-slate-200 hover:bg-white/10'
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </fieldset>
          )}

          {status.hubRegistered === false && status.hubDetail && (
            <p className="text-xs text-amber-300" data-testid="room-detection-hub">
              Not registered with the house yet: {status.hubDetail}
            </p>
          )}
          {error && (
            <p className="text-xs text-amber-300" role="alert" data-testid="room-detection-error">
              {error}
            </p>
          )}
        </>
      )}
    </section>
  );
}
