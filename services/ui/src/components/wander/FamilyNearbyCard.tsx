import { useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { Radio } from 'lucide-react';
import toast from 'react-hot-toast';
import Proximity, { type ProximityDiagnostics } from '../../plugins/proximity';
import { errorText } from '../../lib/errorText';

/** One line on what Family nearby is doing on this device now. */
export function nearbyStatusLine(d: ProximityDiagnostics): string {
  if (!d.enabled) return 'Off on this device.';
  if (!d.scanPermission) return 'Waiting for Nearby devices to be allowed for Jarvis.';
  if (!d.bluetoothOn) return 'Bluetooth is off; it starts again when Bluetooth is on.';
  if (!d.configured) return 'Getting the family key; this needs the internet once.';
  if (d.advertising === 'unsupported') {
    return 'On, listening only: this device cannot send the Bluetooth signal, so it sees family phones but they do not see it.';
  }
  return 'On. Family phones nearby are noticed without Wi-Fi or data.';
}

/**
 * Wander's "Find family nearby" for this Android device. Turning it on is the
 * moment Android 12+ asks for Nearby devices; nothing is asked at app start.
 * Who sees the result follows the family's existing location sharing.
 */
export default function FamilyNearbyCard() {
  const android = Capacitor.getPlatform() === 'android';
  const [diag, setDiag] = useState<ProximityDiagnostics | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!android) return;
    Proximity.getDiagnostics().then(setDiag).catch(() => setDiag(null));
  }, [android]);

  if (!android || !diag) return null;

  const toggle = async () => {
    setBusy(true);
    try {
      const next = diag.enabled ? await Proximity.disable() : await Proximity.enable();
      setDiag(next);
      if (next.enabled && !next.scanPermission) toast.error('Allow Nearby devices for Jarvis to find family phones.');
    } catch (e) {
      toast.error(errorText(e, 'Could not change Family nearby'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-panel space-y-2 rounded-2xl border border-white/5 p-4" data-testid="family-nearby">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <Radio size={15} className="text-emerald-300" /> Find family nearby
        </h3>
        <button
          type="button"
          onClick={() => void toggle()}
          disabled={busy}
          className="min-h-11 rounded-lg px-3 text-xs font-semibold text-purple-300 hover:bg-white/10 disabled:opacity-50"
        >
          {diag.enabled ? 'Turn off' : 'Turn on'}
        </button>
      </div>
      <p className="text-xs text-slate-400">
        Family phones notice each other over Bluetooth, even with no signal, and trips taken together say so.
      </p>
      <p className="text-xs text-slate-300" data-testid="family-nearby-status">{nearbyStatusLine(diag)}</p>
    </div>
  );
}
