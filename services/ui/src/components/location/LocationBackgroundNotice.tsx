import { useCallback, useEffect, useState } from 'react';
import { BatteryWarning, MapPinOff } from 'lucide-react';
import {
  hasBackgroundLocation,
  isBatteryUnrestricted,
  requestBackgroundLocation,
  requestBatteryUnrestricted,
} from '../../lib/locationTracking';

type Check = { background: boolean | null; battery: boolean | null };

/**
 * What Android still needs before location sharing works with the app closed,
 * one step at a time, each opening Android's own screen for it:
 *
 * 1. "Allow all the time". The permission prompt only offers "While using
 *    the app", and with that the phone stops reporting for good the first time
 *    Android reclaims the app, with no error anywhere.
 * 2. Battery optimisation off, or Doze pauses location for hours while the
 *    phone lies still.
 *
 * Re-checked on return from Android's screen; renders nothing once both are
 * set, or when the app cannot tell (web, or an older APK).
 */
export default function LocationBackgroundNotice() {
  const [check, setCheck] = useState<Check>({ background: null, battery: null });

  const refresh = useCallback(() => {
    Promise.all([hasBackgroundLocation(), isBatteryUnrestricted()]).then(([background, battery]) =>
      setCheck({ background, battery }),
    );
  }, []);

  useEffect(() => {
    refresh();
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refresh]);

  if (check.background === false) {
    return (
      <Notice
        icon={<MapPinOff size={16} className="text-amber-400 shrink-0 mt-0.5" />}
        text="Location is set to “While using the app”, so sharing stops soon after you close Jarvis and your family sees where you were hours ago. Choose “Allow all the time”."
        action="Allow all the time"
        onAction={requestBackgroundLocation}
      />
    );
  }
  if (check.battery === false) {
    return (
      <Notice
        icon={<BatteryWarning size={16} className="text-amber-400 shrink-0 mt-0.5" />}
        text="Battery optimisation pauses location sharing while your phone sleeps, so your family may see where you were hours ago."
        action="Allow in background"
        onAction={requestBatteryUnrestricted}
      />
    );
  }
  return null;
}

function Notice({ icon, text, action, onAction }: { icon: React.ReactNode; text: string; action: string; onAction: () => void }) {
  return (
    <div className="flex items-start gap-2.5 p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-xs" data-testid="location-background-notice">
      {icon}
      <div className="flex-1 space-y-2">
        <p className="text-amber-100">{text}</p>
        <button
          type="button"
          onClick={() => onAction()}
          className="min-h-11 px-3 py-1.5 rounded-lg bg-amber-500/20 text-amber-200 font-medium hover:bg-amber-500/30"
        >
          {action}
        </button>
      </div>
    </div>
  );
}
