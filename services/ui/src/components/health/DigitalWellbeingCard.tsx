import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Capacitor } from '@capacitor/core';
import { Smartphone } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import PhoneUsage from '../../plugins/phoneUsage';
import type { UsageDay } from '../../types/api';
import { appName, duration } from '../../lib/phoneUsageFormat';

/**
 * Digital wellbeing on the Health hub (Phase 10): when phone use started and
 * stopped today, total screen time, sessions, the top apps, and the week.
 *
 * Collected data follows the visibility rule the server enforces: an adult
 * sees their own, a parent picks a child, and a minor sees nothing here (their
 * suggestions live on the dashboard; the stream behind them is the parents').
 */

const MINOR_ROLES = new Set(['child', 'teen']);

function clock(epochMs: number | null | undefined): string {
  if (!epochMs) return '—';
  return new Date(epochMs).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function todayKey(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

export default function DigitalWellbeingCard() {
  const { user } = useAuth();
  const me = user?.username || '';
  const selfIsMinor = MINOR_ROLES.has(user?.family_role || '') || Boolean(user?.is_minor);

  // A parent's view: the household's minors. Non-managers get a 403 here and
  // simply see their own card.
  const { data: family = [] } = useQuery({
    queryKey: ['family-roster'],
    queryFn: () => api.getFamily(),
    enabled: Boolean(me) && !selfIsMinor,
    retry: false,
    staleTime: 10 * 60 * 1000,
  });
  const children = useMemo(() => family.filter((m) => m.is_minor && m.username !== me), [family, me]);
  const people = useMemo(
    () => [...(selfIsMinor ? [] : [{ username: me, label: 'You' }]), ...children.map((c) => ({ username: c.username, label: c.display_name || c.username }))],
    [children, me, selfIsMinor],
  );
  const [picked, setPicked] = useState<string | null>(null);
  const subject = picked && people.some((p) => p.username === picked) ? picked : people[0]?.username;

  const { data, isLoading, error } = useQuery({
    queryKey: ['usage', subject],
    queryFn: () => api.getUsage(subject as string, 7),
    enabled: Boolean(subject),
    retry: false,
    staleTime: 60 * 1000,
  });

  if (!subject) return null;

  const refused = isAxiosError(error) && error.response?.status === 403;
  const days: UsageDay[] = data?.days ?? [];
  const today = days.find((d) => d.day === todayKey());
  const week = [...days].reverse();
  const longest = Math.max(1, ...week.map((d) => d.millis || 0));
  const topApps = [...(today?.apps ?? [])].sort((a, b) => b.millis - a.millis).slice(0, 5);
  const viewingSelf = subject === me;
  const canOpenSettings = viewingSelf && Capacitor.getPlatform() === 'android';

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5" data-testid="digital-wellbeing-card">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Smartphone size={16} className="text-sky-300 shrink-0" />
          <h3 className="text-sm font-semibold text-white truncate">Phone use</h3>
        </div>
        {people.length > 1 && (
          <div className="flex gap-1 overflow-x-auto" role="tablist" aria-label="Whose phone use">
            {people.map((p) => (
              <button
                key={p.username}
                type="button"
                role="tab"
                aria-selected={p.username === subject}
                onClick={() => setPicked(p.username)}
                className={`shrink-0 rounded-full px-3 py-1 text-xs min-h-9 ${p.username === subject ? 'bg-white/15 text-white' : 'text-slate-400 hover:text-slate-200'}`}
              >
                {p.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="mt-3 space-y-2">
          <div className="h-3 w-32 skeleton rounded" />
          <div className="h-3 w-full skeleton rounded" />
        </div>
      ) : refused ? (
        <p className="mt-3 text-xs text-slate-400" data-testid="digital-wellbeing-refused">Only their parents can see this.</p>
      ) : error ? (
        <p className="mt-3 text-xs text-amber-300/90" data-testid="digital-wellbeing-error">Phone use could not load.</p>
      ) : !today ? (
        <div className="mt-3 space-y-2" data-testid="digital-wellbeing-empty">
          <p className="text-xs text-slate-400">
            {viewingSelf
              ? 'No phone use reported today. The Android app reports it once Usage Access is on.'
              : 'No phone use reported today from their phone.'}
          </p>
          {canOpenSettings && (
            <button
              type="button"
              onClick={() => void PhoneUsage.openSettings()}
              className="glass-button px-3 py-1.5 text-xs min-h-11"
            >
              Turn on Usage Access
            </button>
          )}
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              { label: 'Screen time', value: duration(today.millis) },
              { label: 'First use', value: clock(today.first_use) },
              { label: 'Last use', value: clock(today.last_use) },
              { label: 'Sessions', value: String(today.sessions ?? 0) },
            ].map((tile) => (
              <div key={tile.label} className="rounded-xl border border-white/10 bg-white/5 px-3 py-2">
                <p className="text-[10px] uppercase tracking-wider text-slate-500">{tile.label}</p>
                <p className="text-sm font-semibold text-slate-100" data-testid={`wellbeing-${tile.label.toLowerCase().replace(' ', '-')}`}>{tile.value}</p>
              </div>
            ))}
          </div>

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
            <div className="flex items-end gap-1 h-12" aria-label="Screen time this week">
              {week.map((d) => (
                <div
                  key={d.day}
                  title={`${d.day}: ${duration(d.millis)}`}
                  className="flex-1 rounded-t bg-sky-400/40"
                  style={{ height: `${Math.max(6, (d.millis / longest) * 100)}%` }}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
