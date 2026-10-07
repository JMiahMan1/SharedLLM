import { useCallback, useEffect, useState } from 'react';
import { BatteryWarning } from 'lucide-react';
import { isBatteryUnrestricted, requestBatteryUnrestricted } from '../../lib/locationTracking';

/**
 * Shown while Android's battery optimisation still restricts the app: with
 * it on, Doze stops location for hours once the phone lies still, and the
 * family map shows the evening before. Re-checked on return from Android's
 * dialog; renders nothing when exempt or when the check is unavailable.
 */
export default function BatteryUnrestrictedNotice() {
  const [unrestricted, setUnrestricted] = useState<boolean | null>(null);

  const refresh = useCallback(() => {
    isBatteryUnrestricted().then(setUnrestricted);
  }, []);

  useEffect(() => {
    refresh();
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refresh]);

  if (unrestricted !== false) return null;
  return (
    <div className="flex items-start gap-2.5 p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-xs">
      <BatteryWarning size={16} className="text-amber-400 shrink-0 mt-0.5" />
      <div className="flex-1 space-y-2">
        <p className="text-amber-100">
          Battery optimisation pauses location sharing while your phone sleeps, so your family may see where you were hours ago.
        </p>
        <button
          type="button"
          onClick={() => requestBatteryUnrestricted()}
          className="px-3 py-1.5 rounded-lg bg-amber-500/20 text-amber-200 font-medium hover:bg-amber-500/30"
        >
          Allow in background
        </button>
      </div>
    </div>
  );
}
