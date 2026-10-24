import { useCallback, useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { useQueryClient } from '@tanstack/react-query';
import { Link2, Link2Off, RefreshCw } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import HealthConnect, { type HealthConnectResult, type HealthConnectStatus } from '../../plugins/healthConnect';

const LABELS: Record<string, string> = {
  sleep: 'sleep',
  resting_heart_rate: 'resting heart rate',
  heart_rate_variability: 'HRV',
  weight: 'weight',
  exercise: 'workouts',
  distance: 'distance',
};

const ERRORS: Record<string, string> = {
  not_connected: 'Not connected.',
  unavailable: 'Health Connect is not available on this phone.',
  signed_out: 'Sign in to sync.',
  different_account: 'Connected by another account on this phone, so nothing is sent for you.',
  no_permission: 'Health Connect has no data access for Jarvis. Connect again to choose what to share.',
  waiting_for_app: 'Waiting for the app to be opened (background reading is off).',
  read_failed: 'Health Connect could not be read.',
  partial_read: 'Some data could not be read; it is tried again next sync.',
  post_failed: 'Could not reach the server; it tries again soon.',
  permissions_unreadable: 'Could not check Health Connect access.',
};

function when(ms: number | null | undefined): string {
  if (!ms) return 'never';
  return new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function summary(result: HealthConnectResult | null): string | null {
  if (!result) return null;
  const parts: string[] = [];
  const added = (result.sleep?.added ?? 0) + (result.metrics?.added ?? 0) + (result.workouts?.added ?? 0);
  if (result.sleep || result.metrics || result.workouts) {
    parts.push(added ? `${added} new reading${added === 1 ? '' : 's'}` : 'nothing new');
  }
  if (result.workouts?.other_types) {
    parts.push(`${result.workouts.other_types} workout${result.workouts.other_types === 1 ? '' : 's'} of a kind Fitness does not list yet`);
  }
  if (result.workouts?.skipped) parts.push(`${result.workouts.skipped} already on Fitness`);
  if (result.error) parts.push(ERRORS[result.error] ?? result.error);
  return parts.length ? parts.join(' · ') : null;
}

/**
 * Health → Health: connect this phone's Health Connect so sleep, resting
 * heart rate, HRV, weight and walks/runs/rides flow in (tagged
 * `health_connect`). Android only; steps are not imported because the phone
 * already counts them.
 */
export default function HealthConnectCard() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const android = Capacitor.getPlatform() === 'android';
  const [status, setStatus] = useState<HealthConnectStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const read = useCallback(() => {
    void HealthConnect.status()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    if (!android) return undefined;
    read();
    // Back from Health Connect's own screens: read again.
    document.addEventListener('visibilitychange', read);
    return () => document.removeEventListener('visibilitychange', read);
  }, [android, read]);

  const refreshHealth = () => {
    // Every Health query (health-summary, health-sleep, ...) and Fitness's workouts.
    void queryClient.invalidateQueries({
      predicate: (query) => {
        const head = String(query.queryKey[0] ?? '');
        return head.startsWith('health') || head.includes('workout');
      },
    });
  };

  const run = async (action: () => Promise<HealthConnectStatus>) => {
    setBusy(true);
    setMessage(null);
    try {
      const next = await action();
      setStatus(next);
      refreshHealth();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'That did not work');
    } finally {
      setBusy(false);
    }
  };

  if (!android || !status) return null;

  const me = user?.username || '';
  const grantedNames = status.granted.map((p) => LABELS[p] ?? p);
  const lastLine = summary(status.lastResult);

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-3" data-testid="health-connect-card">
      <div className="flex items-center gap-2">
        {status.connected ? <Link2 size={16} className="text-emerald-300" /> : <Link2Off size={16} className="text-slate-400" />}
        <h3 className="text-sm font-semibold text-white">Health Connect</h3>
      </div>

      {status.availability !== 'available' ? (
        <div className="space-y-2 text-xs text-slate-300">
          <p data-testid="health-connect-unavailable">
            {status.availability === 'update_required'
              ? 'Health Connect needs to be installed or updated on this phone first.'
              : 'Health Connect is not available on this phone (it needs Android 9 or later).'}
          </p>
          {status.availability === 'update_required' && (
            <button type="button" className="glass-button px-3 py-1.5 min-h-11" onClick={() => void HealthConnect.openHealthConnect()}>
              Get Health Connect
            </button>
          )}
        </div>
      ) : status.connected ? (
        <div className="space-y-2 text-xs text-slate-300">
          <p data-testid="health-connect-on">
            Importing {grantedNames.length ? grantedNames.join(', ') : 'nothing yet'} from Health Connect
            {status.owner && status.owner !== me.toLowerCase() ? ` for ${status.owner}` : ''}. Last sync: {when(status.lastSync)}.
          </p>
          {lastLine && <p className="text-slate-400" data-testid="health-connect-last">{lastLine}</p>}
          {!status.backgroundGranted && (
            <p className="text-slate-400">
              {status.backgroundAvailable
                ? 'Background reading is off, so it syncs when you open Jarvis. Allow it in Health Connect to sync hourly.'
                : 'This phone’s Health Connect only allows reading while Jarvis is open, so it syncs when you open the app.'}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="glass-button px-3 py-1.5 min-h-11 inline-flex items-center gap-1"
              disabled={busy}
              onClick={() => void run(async () => {
                const next = await HealthConnect.syncNow();
                return { ...next, lastResult: next.result };
              })}
            >
              <RefreshCw size={14} className={busy ? 'animate-spin' : ''} /> Sync now
            </button>
            <button type="button" className="glass-button px-3 py-1.5 min-h-11" onClick={() => void HealthConnect.openHealthConnect()}>
              Choose what to share
            </button>
            <button
              type="button"
              className="glass-button px-3 py-1.5 min-h-11"
              disabled={busy}
              onClick={() => void run(() => HealthConnect.disconnect({ revoke: true }))}
            >
              Disconnect
            </button>
          </div>
          <p className="text-[11px] text-slate-500">
            Disconnecting stops the import and removes Jarvis’s access. Readings already imported stay in Health (marked Health Connect).
          </p>
        </div>
      ) : (
        <div className="space-y-2 text-xs text-slate-300">
          <p>
            Bring in sleep, resting heart rate, HRV, weight and walks, runs and rides from a watch, scale or other app
            through Health Connect. Steps are not imported — this phone already counts them.
          </p>
          <button
            type="button"
            className="glass-button px-3 py-1.5 min-h-11"
            disabled={busy || !me}
            onClick={() => void run(() => HealthConnect.connect({ username: me }))}
          >
            Connect Health Connect
          </button>
        </div>
      )}
      {message && <p className="text-xs text-rose-300" role="alert">{message}</p>}
    </section>
  );
}
