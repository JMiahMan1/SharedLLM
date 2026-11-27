import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import { Bluetooth, Clock, Lock, Unlock } from 'lucide-react';
import toast from 'react-hot-toast';
import OfflineMode, { type OfflineCommand } from '../../plugins/offlineMode';
import Nearby from '../../plugins/nearby';
import { errorText } from '../../lib/errorText';
import { commandDeliveryLine } from '../../lib/offlineAnswers';

/**
 * Guardian, beside a child's quick controls (Android app): lock, unlock or
 * give 15 more minutes over Bluetooth through nearby family phones, for when
 * the child's phone (or this one) has no internet, with what became of the
 * last one. Needs offline mode on this phone (Settings > Nearby & offline).
 */
export default function OfflineGuardControls({ username, name }: { username: string; name: string }) {
  const android = Capacitor.getPlatform() === 'android';
  const child = username.toLowerCase();
  const [ready, setReady] = useState<boolean | null>(null);
  const [commands, setCommands] = useState<OfflineCommand[]>([]);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    Nearby.getState().then((s) => setReady(s.offline)).catch(() => setReady(false));
    OfflineMode.commands().then((k) => setCommands(k.commands)).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!android) return undefined;
    reload();
    const t = window.setInterval(reload, 15_000);
    return () => window.clearInterval(t);
  }, [android, reload]);

  if (!android || ready === null) return null;

  const send = async (action: OfflineCommand['action'], minutes = 0) => {
    setBusy(true);
    try {
      await OfflineMode.sendCommand({ child, action, minutes });
      toast.success('Sent to family phones nearby');
      reload();
    } catch (e) {
      toast.error(errorText(e, 'Could not send that'));
    } finally {
      setBusy(false);
    }
  };

  const last = commands.filter((k) => k.child === child).sort((a, b) => b.issued_at - a.issued_at)[0];

  return (
    <section
      className="glass-panel space-y-2 rounded-2xl border border-white/10 p-3.5 sm:p-4"
      aria-label={`${name}: without internet`}
      data-testid="guardian-offline-controls"
    >
      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
        <Bluetooth size={13} aria-hidden /> Without internet
      </h3>
      {!ready ? (
        <div className="space-y-1" data-testid="guardian-offline-off">
          <p className="text-xs text-slate-300">
            Turn on Offline mode on this phone to reach {name}&apos;s phone through nearby family phones when there is no internet.
          </p>
          <Link to="/settings#nearby-offline"
            className="inline-flex min-h-11 items-center rounded-xl px-2 text-xs font-semibold text-purple-300 hover:bg-white/5">
            Settings, Nearby &amp; offline
          </Link>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-2 min-[380px]:grid-cols-3" role="group" aria-label={`Reach ${name}'s phone without internet`}>
            <button type="button" disabled={busy} onClick={() => void send('lock')}
              className="inline-flex min-h-11 items-center justify-center gap-1 rounded-xl bg-red-500/20 px-3 text-xs font-semibold text-red-100 disabled:opacity-50">
              <Lock size={13} aria-hidden /> Lock
            </button>
            <button type="button" disabled={busy} onClick={() => void send('unlock')}
              className="inline-flex min-h-11 items-center justify-center gap-1 rounded-xl bg-emerald-500/20 px-3 text-xs font-semibold text-emerald-100 disabled:opacity-50">
              <Unlock size={13} aria-hidden /> Unlock
            </button>
            <button type="button" disabled={busy} onClick={() => void send('bonus', 15)}
              className="inline-flex min-h-11 items-center justify-center gap-1 rounded-xl bg-white/10 px-3 text-xs font-semibold text-sky-100 disabled:opacity-50">
              <Clock size={13} aria-hidden /> 15 more minutes
            </button>
          </div>
          {last && <p className="text-xs text-slate-300" data-testid="offline-delivery">{commandDeliveryLine(last, name)}</p>}
        </>
      )}
    </section>
  );
}
