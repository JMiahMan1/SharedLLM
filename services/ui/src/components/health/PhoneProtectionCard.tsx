import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Capacitor } from '@capacitor/core';
import { ShieldCheck, ShieldAlert } from 'lucide-react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import Guard, { type GuardStatus } from '../../plugins/guard';
import PhoneUsage from '../../plugins/phoneUsage';
import MediaSession from '../../plugins/mediaSession';
import Comms from '../../plugins/comms';
import { readReportingAccess, type ReportingAccess } from '../../lib/reportingAccess';

/**
 * Setting up Jarvis's protection on a child's Android phone: device admin (no
 * plain uninstall) and the screen guard (removing or switching off Jarvis
 * needs a parent's login; screen-time rules are enforced). Android only lets
 * the person holding the phone switch these on, so this walks a parent through
 * it on the child's phone, and shows plainly when both are on. The same goes
 * for what the parents' reports read (usage access for screen time and apps,
 * notification access for media and chat apps, call and message logs):
 * without them the phone reports nothing and nothing says why.
 */
export default function PhoneProtectionCard() {
  const { user } = useAuth();
  const me = user?.username || '';
  const android = Capacitor.getPlatform() === 'android';
  const [guard, setGuard] = useState<GuardStatus | null>(null);
  const [access, setAccess] = useState<ReportingAccess | null>(null);

  const { data: status } = useQuery({
    queryKey: ['screen-time', me],
    queryFn: () => api.getScreenTimeStatus(me),
    enabled: Boolean(me) && android,
    retry: false,
  });

  useEffect(() => {
    if (!android) return undefined;
    let alive = true;
    const read = () => {
      void Guard.status().then((s) => alive && setGuard(s)).catch(() => undefined);
      void readReportingAccess().then((a) => alive && setAccess(a));
    };
    read();
    // Coming back from Settings: read it again.
    document.addEventListener('visibilitychange', read);
    return () => {
      alive = false;
      document.removeEventListener('visibilitychange', read);
    };
  }, [android]);

  if (!android || !status?.protected || !guard) return null;
  const filterWanted = Boolean(status.rules?.web_filter?.enabled);
  const filterReady = !filterWanted || Boolean(guard.webFilterAllowed);
  const reportingOn = Boolean(access && access.usage && access.notifications && access.calls && access.sms);
  const allOn = guard.adminActive && guard.guardEnabled && filterReady && reportingOn;
  const askComms = () => void Comms.requestAccess().then(() => readReportingAccess().then(setAccess)).catch(() => undefined);

  return (
    <section className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5 space-y-3" data-testid="phone-protection-card">
      <div className="flex items-center justify-between gap-2 border-b border-white/5 pb-3">
        <div className="flex items-center gap-2.5">
          <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border ${allOn ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300' : 'border-amber-400/30 bg-amber-400/10 text-amber-300'}`}>
            {allOn ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}
          </div>
          <h3 className="text-sm font-semibold text-white">Phone protection</h3>
        </div>
        <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${allOn ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200' : 'border-amber-400/30 bg-amber-400/10 text-amber-200'}`}>
          {allOn ? 'Active' : 'Setup needed'}
        </span>
      </div>
      {allOn ? (
        <p className="rounded-xl border border-emerald-400/20 bg-emerald-400/5 p-3 text-xs leading-relaxed text-slate-200" data-testid="phone-protection-on">
          Jarvis is protected on this phone{guard.deviceOwner ? ' (device owner)' : ''}. Removing it or switching this off needs a parent.
          {filterWanted && (guard.webFilterActive ? ' The web filter is on.' : ' The web filter starts within a minute.')}
        </p>
      ) : (
        <>
          <p className="text-xs text-slate-300">A parent sets this up once, on this phone:</p>
          <ol className="list-decimal pl-5 space-y-2.5 text-xs text-slate-300">
            <li className="rounded-xl border border-white/5 bg-white/[0.02] p-2.5">
              Device protection{' '}
              {guard.adminActive ? (
                <span className="font-medium text-emerald-300">is on.</span>
              ) : (
                <button type="button" className="glass-button ml-1 rounded-xl px-3 py-1.5 min-h-11 font-medium" onClick={() => void Guard.enableAdmin()}>
                  Turn on
                </button>
              )}
            </li>
            <li className="rounded-xl border border-white/5 bg-white/[0.02] p-2.5">
              Screen guard{' '}
              {guard.guardEnabled ? (
                <span className="font-medium text-emerald-300">is on.</span>
              ) : (
                <>
                  <button type="button" className="glass-button ml-1 rounded-xl px-3 py-1.5 min-h-11 font-medium" onClick={() => void Guard.openGuardSettings()}>
                    Open Accessibility
                  </button>
                  <span className="block mt-1.5 leading-relaxed text-slate-400">
                    Choose <b>Jarvis screen guard</b> and switch it on. If Android says it is a restricted setting,{' '}
                    <button type="button" className="min-h-11 px-1 underline text-sky-300" onClick={() => void Guard.openAppInfo()}>
                      open App info
                    </button>
                    , tap ⋮, then <b>Allow restricted settings</b>, and try again.
                  </span>
                </>
              )}
            </li>
            <li className="rounded-xl border border-white/5 bg-white/[0.02] p-2.5" data-testid="phone-protection-usage">
              Usage access{' '}
              {access?.usage ? (
                <span className="font-medium text-emerald-300">is on.</span>
              ) : (
                <>
                  <button type="button" className="glass-button ml-1 rounded-xl px-3 py-1.5 min-h-11 font-medium" onClick={() => void PhoneUsage.openSettings()}>
                    Turn on
                  </button>
                  <span className="block mt-1.5 leading-relaxed text-slate-400">
                    Choose <b>Jarvis</b> and allow usage access. Screen time, app limits and the apps a parent sees all come from it.
                  </span>
                </>
              )}
            </li>
            <li className="rounded-xl border border-white/5 bg-white/[0.02] p-2.5" data-testid="phone-protection-notifications">
              Notification access{' '}
              {access?.notifications ? (
                <span className="font-medium text-emerald-300">is on.</span>
              ) : (
                <>
                  <button type="button" className="glass-button ml-1 rounded-xl px-3 py-1.5 min-h-11 font-medium" onClick={() => void MediaSession.openSettings()}>
                    Turn on
                  </button>
                  <span className="block mt-1.5 leading-relaxed text-slate-400">Choose <b>Jarvis</b>. Music, videos and chat apps are reported with it.</span>
                </>
              )}
            </li>
            <li className="rounded-xl border border-white/5 bg-white/[0.02] p-2.5" data-testid="phone-protection-comms">
              Calls and messages{' '}
              {access?.calls && access?.sms ? (
                <span className="font-medium text-emerald-300">are allowed.</span>
              ) : (
                <button type="button" className="glass-button ml-1 rounded-xl px-3 py-1.5 min-h-11 font-medium" onClick={askComms}>
                  Allow
                </button>
              )}
            </li>
            {filterWanted && (
              <li className="rounded-xl border border-white/5 bg-white/[0.02] p-2.5">
                Web filter{' '}
                {guard.webFilterAllowed ? (
                  <span className="font-medium text-emerald-300">is allowed.</span>
                ) : (
                  <>
                    <button type="button" className="glass-button ml-1 rounded-xl px-3 py-1.5 min-h-11 font-medium" onClick={() => void Guard.allowWebFilter()}>
                      Allow
                    </button>
                    <span className="block mt-1.5 leading-relaxed text-slate-400">
                      Android asks once whether Jarvis may filter websites (it shows as a VPN). Tap <b>OK</b>.
                    </span>
                  </>
                )}
              </li>
            )}
          </ol>
        </>
      )}
      {!guard.deviceOwner && (
        <div className="space-y-1.5 rounded-xl border border-white/10 bg-black/25 p-3.5" data-testid="phone-protection-device-owner">
          <p className="text-xs leading-relaxed text-slate-300">
            For a real lock (and to make Jarvis impossible to uninstall), set this phone up as a{' '}
            <b>device owner</b>. On a computer with <code>adb</code>, run this once:
          </p>
          <code
            className="block break-words rounded-lg bg-black/40 px-2.5 py-1.5 font-mono text-[11px] text-emerald-200"
            data-testid="phone-protection-device-owner-command"
          >
            adb shell dpm set-device-owner com.jarvisos.app/.GuardAdminReceiver
          </code>
          <p className="text-[11px] leading-relaxed text-slate-400">
            This only works on a phone with <b>no accounts signed in</b> (a freshly factory-reset phone). Android refuses it
            otherwise, so remove every account or factory reset the phone, run the command before signing in, then sign in to Jarvis.
          </p>
        </div>
      )}
    </section>
  );
}
