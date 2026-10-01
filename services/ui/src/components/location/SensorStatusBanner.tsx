import { useState } from 'react';
import { AlertTriangle, Loader2, MapPinOff, ShieldAlert, Footprints } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import toast from 'react-hot-toast';
import { useLocation, type SensorId } from '../../context/LocationContext';
import { sensorStatus, isHealthy, worstOf, type SensorStatusView } from '../../lib/sensorStatus';

/**
 * Makes a stalled sensor impossible to miss.
 *
 * Renders nothing at all when both sensors are healthy, so it adds zero noise in
 * the common case. When something is wrong it states what broke, what it costs
 * the user's data, and either offers a one-tap re-enable or points at the phone's
 * OS settings — whichever is actually true.
 *
 * Mobile parity: every control is ≥44px on coarse pointers, and there is no
 * hover-only affordance (the whole card is the tap target where it can be).
 */

const SENSOR_ICON: Record<string, LucideIcon> = {
  location: MapPinOff,
  steps: Footprints,
};

const HEALTH_TONE: Record<string, string> = {
  // Recovering is deliberately calm: the app is fixing it and we do not want to
  // alarm someone about a 5-second retry.
  recovering: 'border-sky-400/30 bg-sky-500/10 text-sky-100',
  off: 'border-amber-400/30 bg-amber-500/10 text-amber-100',
  denied: 'border-rose-400/30 bg-rose-500/10 text-rose-100',
  unavailable: 'border-white/15 bg-white/5 text-slate-300',
};

function toneFor(health: string): string {
  return HEALTH_TONE[health] ?? HEALTH_TONE.unavailable;
}

interface ActionProps {
  view: SensorStatusView;
  sensor: SensorId;
  busy: boolean;
  onEnable: (id: SensorId) => void;
  onOpenSettings: (id: SensorId) => void;
}

/** One sensor's notice: icon, headline, data consequence, and the one action that helps. */
function SensorNotice({ view, sensor, busy, onEnable, onOpenSettings }: ActionProps) {
  if (isHealthy(view)) return null;
  const Icon = SENSOR_ICON[sensor] ?? AlertTriangle;

  return (
    <li
      className={`flex flex-col gap-3 rounded-xl border p-3 sm:flex-row sm:items-center sm:justify-between ${toneFor(view.health)}`}
      data-testid={`sensor-notice-${sensor}`}
      data-health={view.health}
    >
      <div className="flex min-w-0 items-start gap-3">
        {view.health === 'recovering' ? (
          <Loader2 size={18} className="mt-0.5 shrink-0 animate-spin" aria-hidden />
        ) : (
          <Icon size={18} className="mt-0.5 shrink-0" aria-hidden />
        )}
        <div className="min-w-0">
          <p className="text-sm font-semibold">
            {view.health === 'recovering' && <span className="sr-only">Reconnecting: </span>}
            {view.title}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed opacity-90">{view.detail}</p>
        </div>
      </div>

      <div className="flex shrink-0 gap-2">
        {view.canEnable && (
          <button
            type="button"
            onClick={() => onEnable(sensor)}
            disabled={busy}
            data-testid={`sensor-enable-${sensor}`}
            className="min-h-11 flex-1 rounded-lg bg-white/15 px-4 text-xs font-bold uppercase tracking-wide transition hover:bg-white/25 disabled:opacity-50 sm:flex-none pointer-coarse:min-h-11"
          >
            {busy ? 'Turning on…' : 'Turn on'}
          </button>
        )}
        {view.needsOsSettings && (
          <button
            type="button"
            onClick={() => onOpenSettings(sensor)}
            disabled={busy}
            data-testid={`sensor-settings-${sensor}`}
            className="min-h-11 flex-1 rounded-lg bg-white/15 px-4 text-xs font-bold uppercase tracking-wide transition hover:bg-white/25 disabled:opacity-50 sm:flex-none pointer-coarse:min-h-11"
          >
            How to allow
          </button>
        )}
      </div>
    </li>
  );
}

export const SensorStatusBanner = () => {
  const { sensors, enableSensor, openSensorSettings } = useLocation();
  const [busy, setBusy] = useState<SensorId | null>(null);

  const location = sensorStatus('location', sensors.location);
  const steps = sensorStatus('steps', sensors.steps);
  const views: Record<SensorId, SensorStatusView> = { location, steps };

  const worst = worstOf([location, steps]);
  // Nothing to say when both sensors are healthy.
  if (!worst) return null;

  const handleEnable = async (id: SensorId) => {
    setBusy(id);
    try {
      const ok = await enableSensor(id);
      if (ok) toast.success(`${id === 'location' ? 'Location sharing' : 'Step tracking'} is on`);
      // enableSensor already toasts the specific failure; do not double-report.
    } catch {
      toast.error('Could not turn that on — check your phone permissions');
    } finally {
      setBusy(null);
    }
  };

  const handleOpenSettings = async (id: SensorId) => {
    setBusy(id);
    try {
      await openSensorSettings(id);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section
      aria-label="Tracking status"
      data-testid="sensor-status-banner"
      className="glass-panel rounded-2xl border border-white/10 p-4"
    >
      <header className="mb-3 flex items-center gap-2">
        <ShieldAlert size={16} className="shrink-0 text-slate-300" aria-hidden />
        <h2 className="text-xs font-bold uppercase tracking-widest text-slate-300">
          Tracking isn&apos;t reporting
        </h2>
      </header>

      {/* Collapsed summary on phones; the per-sensor list stays available below. */}
      <p className="mb-3 text-xs leading-relaxed text-slate-300 sm:hidden">{worst.title}</p>

      <ul className="flex flex-col gap-2">
        {(['location', 'steps'] as SensorId[]).map((id) => (
          <SensorNotice
            key={id}
            sensor={id}
            view={views[id]}
            busy={busy === id}
            onEnable={handleEnable}
            onOpenSettings={handleOpenSettings}
          />
        ))}
      </ul>
    </section>
  );
};

export default SensorStatusBanner;