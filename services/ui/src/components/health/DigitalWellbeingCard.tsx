import { useQuery } from '@tanstack/react-query';
import { Capacitor } from '@capacitor/core';
import { Smartphone } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import PhoneUsage from '../../plugins/phoneUsage';
import type { UsageDay } from '../../types/api';
import { appName, duration } from '../../lib/phoneUsageFormat';
import { usageQuery } from '../../lib/memberPage';
import { localDay } from '../../lib/healthHub';
import UsageTiles from './UsageTiles';

/** Single-letter weekday for a `YYYY-MM-DD`, read at noon so no zone shifts the day. */
function weekdayInitial(day: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: 'narrow' });
}

/**
 * Digital wellbeing on the Health hub (Phase 10): the signed-in adult's own
 * phone use today (when it started and stopped, screen time, sessions, the
 * top apps) and the week.
 *
 * A child's phone use, and every control over it, live on their family member
 * page (the parents' link above this card). A minor sees nothing here: their
 * suggestions live on the dashboard; the stream behind them is the parents'.
 */
export default function DigitalWellbeingCard() {
  const { user } = useAuth();
  const me = user?.username || '';
  const selfIsMinor = user?.family_role === 'child' || user?.family_role === 'teen' || Boolean(user?.is_minor);

  const { data, isLoading, error } = useQuery({ ...usageQuery(me), enabled: Boolean(me) && !selfIsMinor });

  if (!me || selfIsMinor) return null;

  const days: UsageDay[] = data?.days ?? [];
  const today = days.find((d) => d.day === localDay());
  const week = [...days].reverse().slice(-7);
  const longest = Math.max(1, ...week.map((d) => d.millis || 0));
  const topApps = [...(today?.apps ?? [])].sort((a, b) => b.millis - a.millis).slice(0, 5);
  const canOpenSettings = Capacitor.getPlatform() === 'android';

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5" data-testid="digital-wellbeing-card">
      <div className="flex items-center gap-2 min-w-0">
        <Smartphone size={16} className="text-sky-300 shrink-0" />
        <h3 className="text-sm font-semibold text-white truncate">Phone use</h3>
      </div>

      {isLoading ? (
        <div className="mt-3 space-y-2">
          <div className="h-3 w-32 skeleton rounded" />
          <div className="h-3 w-full skeleton rounded" />
        </div>
      ) : error ? (
        <p className="mt-3 text-xs text-amber-300/90" data-testid="digital-wellbeing-error">Phone use could not load.</p>
      ) : !today ? (
        <div className="mt-3 space-y-2" data-testid="digital-wellbeing-empty">
          <p className="text-xs text-slate-400">No phone use reported today. The Android app reports it once Usage Access is on.</p>
          {canOpenSettings && (
            <button type="button" onClick={() => void PhoneUsage.openSettings()} className="glass-button px-3 py-1.5 text-xs min-h-11">
              Turn on Usage Access
            </button>
          )}
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <UsageTiles day={today} />

          {topApps.length > 0 && (
            <ul className="space-y-1.5" aria-label="Top apps today">
              {topApps.map((app) => (
                <li key={app.package} className="flex items-center justify-between gap-2 text-xs">
                  <span className="truncate text-slate-200">{appName(app.package)}</span>
                  <span className="shrink-0 text-slate-400">{duration(app.millis)}</span>
                </li>
              ))}
            </ul>
          )}

          {week.length > 1 && (
            <div aria-label="Screen time this week">
              <div className="flex items-end gap-1 h-12">
                {week.map((d) => (
                  <div
                    key={d.day}
                    title={`${d.day}: ${duration(d.millis)}`}
                    className="flex-1 rounded-t bg-sky-400/40"
                    style={{ height: `${Math.max(6, (d.millis / longest) * 100)}%` }}
                  />
                ))}
              </div>
              {/* The bars had no axis, so a taller Tuesday said nothing about
                  which day it was. The exact minutes stay in the tooltip. */}
              <div className="mt-1 flex gap-1" aria-hidden>
                {week.map((d) => (
                  <span key={d.day} className="flex-1 text-center text-[9px] uppercase text-slate-500">
                    {weekdayInitial(d.day)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
