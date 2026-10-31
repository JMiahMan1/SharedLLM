import { useEffect, useState } from 'react';
import { Capacitor } from '@capacitor/core';
import { Check, ClipboardList } from 'lucide-react';
import PhoneUsage from '../../plugins/phoneUsage';
import MediaSession from '../../plugins/mediaSession';
import Comms from '../../plugins/comms';
import { readReportingAccess, type ReportingAccess } from '../../lib/reportingAccess';
import { requestActivityAccess } from '../../lib/locationTracking';

/**
 * What this phone lets Jarvis read for the family's reports (screen time,
 * media and chat notifications, calls, texts), with a button for each that
 * is off. Before this only a protected child's phone was ever asked, so no
 * other phone sent a single call or text. Android only.
 */
export default function PhoneReportsPanel() {
  const android = Capacitor.getPlatform() === 'android';
  const [access, setAccess] = useState<ReportingAccess | null>(null);

  useEffect(() => {
    if (!android) return undefined;
    let alive = true;
    const read = () => void readReportingAccess().then((a) => alive && setAccess(a));
    read();
    // Coming back from Android's settings: read it again.
    document.addEventListener('visibilitychange', read);
    return () => {
      alive = false;
      document.removeEventListener('visibilitychange', read);
    };
  }, [android]);

  if (!android || !access) return null;
  const askComms = () => void Comms.requestAccess().then(() => readReportingAccess().then(setAccess)).catch(() => undefined);
  const askActivity = () => void requestActivityAccess().then(() => readReportingAccess().then(setAccess)).catch(() => undefined);
  const rows: { key: keyof ReportingAccess; label: string; what: string; allow: () => void }[] = [
    { key: 'usage', label: 'Usage access', what: 'Screen time and app use', allow: () => void PhoneUsage.openSettings() },
    { key: 'notifications', label: 'Notification access', what: 'Media playing, chat and social apps', allow: () => void MediaSession.openSettings() },
    { key: 'calls', label: 'Call log', what: 'Calls made and received', allow: askComms },
    { key: 'sms', label: 'Text messages', what: 'Texts sent and received', allow: askComms },
    // An app build without it reports null: no row rather than a dead button.
    ...(access.activity === null
      ? []
      : [{ key: 'activity' as const, label: 'Physical activity', what: 'Knowing when you are driving, for trips and driving reports', allow: askActivity }]),
  ];

  return (
    <section className="glass-panel rounded-2xl p-4 space-y-2" aria-labelledby="phone-reports-heading" data-testid="phone-reports-panel">
      <div className="px-1">
        <h2 id="phone-reports-heading" className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
          <ClipboardList size={16} className="text-purple-400" />
          Phone reports
        </h2>
        <p className="text-xs text-slate-500 mt-1">
          What this phone shares with the family's activity reports. Each needs Android's permission once.
        </p>
      </div>
      <ul className="space-y-1">
        {rows.map((row) => (
          <li key={row.key} className="flex items-center justify-between gap-3 rounded-xl bg-white/5 p-3" data-testid={`phone-report-${row.key}`}>
            <div className="min-w-0">
              <p className="text-sm font-medium text-white">{row.label}</p>
              <p className="text-xs text-slate-400">{row.what}</p>
            </div>
            {access[row.key] ? (
              <span className="inline-flex shrink-0 items-center gap-1 text-xs text-emerald-300"><Check size={13} /> Allowed</span>
            ) : (
              <button type="button" onClick={row.allow} className="glass-button min-h-11 shrink-0 rounded-xl px-3 text-xs font-semibold">
                Allow
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
