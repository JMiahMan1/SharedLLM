import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Capacitor } from '@capacitor/core';
import { ShieldCheck, ShieldAlert } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import Guard, { type GuardStatus } from '../../plugins/guard';

/**
 * Setting up Jarvis's protection on a child's Android phone: device admin (no
 * plain uninstall) and the screen guard (removing or switching off Jarvis
 * needs a parent's login; screen-time rules are enforced). Android only lets
 * the person holding the phone switch these on, so this walks a parent through
 * it on the child's phone, and shows plainly when both are on.
 */
export default function PhoneProtectionCard() {
  const { user } = useAuth();
  const me = user?.username || '';
  const android = Capacitor.getPlatform() === 'android';
  const [guard, setGuard] = useState<GuardStatus | null>(null);

  const { data: status } = useQuery({
    queryKey: ['screen-time', me],
    queryFn: () => api.getScreenTimeStatus(me),
    enabled: Boolean(me) && android,
    retry: false,
  });

  useEffect(() => {
    if (!android) return undefined;
    let alive = true;
    const read = () => void Guard.status().then((s) => alive && setGuard(s)).catch(() => undefined);
    read();
    // Coming back from Settings: read it again.
    document.addEventListener('visibilitychange', read);
    return () => {
      alive = false;
      document.removeEventListener('visibilitychange', read);
    };
  }, [android]);

  if (!android || !status?.protected || !guard) return null;
  const allOn = guard.adminActive && guard.guardEnabled;

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-3" data-testid="phone-protection-card">
      <div className="flex items-center gap-2">
        {allOn ? <ShieldCheck size={16} className="text-emerald-300" /> : <ShieldAlert size={16} className="text-amber-300" />}
        <h3 className="text-sm font-semibold text-white">Phone protection</h3>
      </div>
      {allOn ? (
        <p className="text-xs text-slate-300" data-testid="phone-protection-on">
          Jarvis is protected on this phone{guard.deviceOwner ? ' (device owner)' : ''}. Removing it or switching this off needs a parent.
        </p>
      ) : (
        <>
          <p className="text-xs text-slate-300">A parent sets this up once, on this phone:</p>
          <ol className="list-decimal pl-5 space-y-2 text-xs text-slate-300">
            <li>
              Device protection{' '}
              {guard.adminActive ? (
                <span className="text-emerald-300">is on.</span>
              ) : (
                <button type="button" className="glass-button ml-1 px-3 py-1.5 min-h-11" onClick={() => void Guard.enableAdmin()}>
                  Turn on
                </button>
              )}
            </li>
            <li>
              Screen guard{' '}
              {guard.guardEnabled ? (
                <span className="text-emerald-300">is on.</span>
              ) : (
                <>
                  <button type="button" className="glass-button ml-1 px-3 py-1.5 min-h-11" onClick={() => void Guard.openGuardSettings()}>
                    Open Accessibility
                  </button>
                  <span className="block mt-1 text-slate-400">
                    Choose <b>Jarvis screen guard</b> and switch it on. If Android says it is a restricted setting,{' '}
                    <button type="button" className="underline" onClick={() => void Guard.openAppInfo()}>
                      open App info
                    </button>
                    , tap ⋮, then <b>Allow restricted settings</b>, and try again.
                  </span>
                </>
              )}
            </li>
          </ol>
        </>
      )}
    </section>
  );
}
