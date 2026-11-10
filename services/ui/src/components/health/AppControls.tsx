import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AppWindow, Check } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { appName, duration } from '../../lib/phoneUsageFormat';
import type { PermissionGroup, ScreenRules } from '../../types/api';
import { PERMISSION_WORDS } from '../../lib/guardRequests';
import { guardRequestsQuery, usageQuery } from '../../lib/memberPage';
import { localDay, shiftDay } from '../../lib/healthHub';
import { useMemberDeviceNoun } from '../../hooks/useDeviceNoun';
import MemberCard from '../member/MemberCard';
import Toggle from '../ui/Toggle';

/**
 * A parent's app control for one child (Phase 11 step 5): switch an app off,
 * or give it its own daily minutes. The apps listed are the ones the child's
 * phone actually reported this week, plus any app that already has a rule or
 * is waiting for a parent's OK, each with today's and the week's time and its
 * state (allowed, limited, blocked, awaiting approval).
 * The phone's screen guard closes a blocked app; the rest of the phone works.
 *
 * Also: whether a newly installed app waits for a parent's OK (on by
 * default), and which kinds of permission an app may get only once a parent
 * approves it for that app. The requests themselves land on the parents' page.
 */
export default function AppControls({ username, name, rules }: { username: string; name: string; rules: ScreenRules }) {
  const device = useMemberDeviceNoun(username);
  const queryClient = useQueryClient();
  const { data: usage } = useQuery(usageQuery(username));
  const { data: pending } = useQuery(guardRequestsQuery(false));
  const [limitDraft, setLimitDraft] = useState<Record<string, string>>({});
  const awaiting = useMemo(
    () => new Set((pending || []).filter((r) => r.username === username && r.kind === 'app').map((r) => r.target)),
    [pending, username],
  );

  const { apps, todayMs } = useMemo(() => {
    const today = localDay();
    const weekStart = shiftDay(today, -6);
    const week = new Map<string, number>();
    const todayMs = new Map<string, number>();
    for (const day of usage?.days || []) {
      if (day.day < weekStart) continue;
      for (const app of day.apps || []) {
        week.set(app.package, (week.get(app.package) || 0) + app.millis);
        if (day.day === today) todayMs.set(app.package, app.millis);
      }
    }
    for (const pkg of [...(rules.blocked_apps || []), ...Object.keys(rules.app_limits || {}), ...awaiting]) {
      if (!week.has(pkg)) week.set(pkg, 0);
    }
    const allowed = new Set(rules.always_allowed || []);
    return {
      apps: [...week.entries()].filter(([pkg]) => !allowed.has(pkg)).sort((a, b) => b[1] - a[1]).slice(0, 25),
      todayMs,
    };
  }, [usage, rules, awaiting]);

  const save = useMutation({
    mutationFn: (next: ScreenRules) => api.setScreenRules(username, next),
    onSuccess: () => {
      setLimitDraft({});
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
    },
  });

  const blocked = new Set(rules.blocked_apps || []);
  const limits = rules.app_limits || {};
  // The full rules go back each time: the server replaces them as a whole.
  const withRules = (patch: ScreenRules): ScreenRules => {
    const rest: ScreenRules = { ...rules };
    delete rest.paused;
    delete rest.paused_until;
    return { ...rest, ...patch };
  };
  const withApps = (blockedApps: string[], appLimits: Record<string, number>): ScreenRules =>
    withRules({ blocked_apps: blockedApps, app_limits: appLimits });
  const approvals = rules.install_approval !== false;
  const gated = new Set<PermissionGroup>(rules.permission_gating || []);
  const toggleGroup = (group: PermissionGroup) => {
    const next = new Set(gated);
    if (next.has(group)) next.delete(group);
    else next.add(group);
    save.mutate(withRules({ permission_gating: [...next] }));
  };
  const toggle = (pkg: string) =>
    save.mutate(withApps(blocked.has(pkg) ? [...blocked].filter((p) => p !== pkg) : [...blocked, pkg], limits));
  const setLimit = (pkg: string) => {
    const raw = (limitDraft[pkg] ?? '').trim();
    const next = { ...limits };
    if (raw === '') delete next[pkg];
    else next[pkg] = Math.max(0, Math.min(1440, Math.round(Number(raw))));
    save.mutate(withApps([...blocked], next));
  };

  const failure = isAxiosError(save.error) ? (save.error.response?.data as { detail?: string })?.detail || 'That did not save.' : save.error ? 'That did not save.' : null;

  const shown = apps.length;
  return (
    <MemberCard
      title={`Apps on ${name}'s ${device}`}
      icon={AppWindow}
      testId="app-controls"
      aside={shown ? [blocked.size && `${blocked.size} off`, Object.keys(limits).length && `${Object.keys(limits).length} limited`].filter(Boolean).join(' · ') || undefined : undefined}
    >
      {apps.length === 0 ? (
        <p className="text-[11px] text-slate-400">No app use reported yet. Apps show up here once {name}'s {device} shares its usage.</p>
      ) : (
        <ul className="divide-y divide-white/5">
          {apps.map(([pkg, millis]) => {
            const off = blocked.has(pkg);
            const draft = limitDraft[pkg] ?? (limits[pkg] !== undefined ? String(limits[pkg]) : '');
            const state = awaiting.has(pkg) ? 'awaiting approval' : off ? 'blocked' : limits[pkg] !== undefined ? `limited to ${limits[pkg]} min` : 'allowed';
            const tone = awaiting.has(pkg) ? 'text-sky-300' : off ? 'text-red-300' : limits[pkg] !== undefined ? 'text-amber-300' : 'text-slate-400';
            return (
              <li key={pkg} className="space-y-1.5 py-2.5 text-xs first:pt-0 last:pb-0" data-testid={`app-row-${pkg}`}>
                <div className="flex items-center gap-3">
                  <span className={`min-w-0 flex-1 truncate text-sm ${off ? 'text-slate-500 line-through' : 'text-slate-100'}`}>{appName(pkg)}</span>
                  <Toggle checked={!off} ariaLabel={`${appName(pkg)} allowed`} disabled={save.isPending} onChange={() => toggle(pkg)} />
                </div>
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 text-[11px] text-slate-500">
                    {todayMs.get(pkg) ? `${duration(todayMs.get(pkg) || 0)} today · ` : ''}
                    {millis ? `${duration(millis)} this week` : 'not used this week'} · <span className={tone} data-testid={`app-state-${pkg}`}>{state}</span>
                  </span>
                  {!off && (
                    <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-slate-400">
                      <input
                        type="number"
                        min={0}
                        max={1440}
                        inputMode="numeric"
                        placeholder="no limit"
                        aria-label={`Daily minutes for ${appName(pkg)}`}
                        value={draft}
                        onChange={(e) => setLimitDraft({ ...limitDraft, [pkg]: e.target.value })}
                        onBlur={() => { if (limitDraft[pkg] !== undefined) setLimit(pkg); }}
                        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
                        disabled={save.isPending}
                        className="min-h-11 w-20 rounded-xl border border-white/10 bg-white/5 px-2 text-center text-sm text-slate-100 placeholder:text-[11px] placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none"
                      />
                      min/day
                    </label>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5">
        <div className="min-w-0 text-xs">
          <p className="font-medium text-slate-100">New apps wait for a parent's OK</p>
          <p className="text-[11px] text-slate-500">A new app stays closed until a parent approves it.</p>
        </div>
        <Toggle
          checked={approvals}
          ariaLabel="New apps wait for a parent's OK"
          testId="install-approval"
          disabled={save.isPending}
          onChange={() => save.mutate(withRules({ install_approval: !approvals }))}
        />
      </div>
      <div className="space-y-1.5">
        <p className="text-[11px] text-slate-400">Ask a parent before an app gets:</p>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Permissions a parent approves first" data-testid="permission-gating">
          {(Object.keys(PERMISSION_WORDS) as PermissionGroup[]).map((group) => {
            const on = gated.has(group);
            return (
              <button
                key={group}
                type="button"
                aria-pressed={on}
                disabled={save.isPending}
                onClick={() => toggleGroup(group)}
                className={`inline-flex min-h-11 items-center gap-1 rounded-xl border px-2.5 text-[11px] transition-colors ${on ? 'border-sky-400/40 bg-sky-400/15 text-white' : 'border-white/10 bg-white/5 text-slate-400 hover:text-slate-200'}`}
              >
                {on && <Check size={12} aria-hidden />}
                {PERMISSION_WORDS[group]}
              </button>
            );
          })}
        </div>
      </div>
      {failure && <p className="text-xs text-amber-300" role="alert">{failure}</p>}
    </MemberCard>
  );
}
