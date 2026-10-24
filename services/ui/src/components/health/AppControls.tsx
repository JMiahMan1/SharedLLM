import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AppWindow } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { appName, duration } from '../../lib/phoneUsageFormat';
import type { ScreenRules } from '../../types/api';

/**
 * A parent's app control for one child (Phase 11 step 5): switch an app off,
 * or give it its own daily minutes. The apps listed are the ones the child's
 * phone actually reported this week, plus any app that already has a rule.
 * The phone's screen guard closes a blocked app; the rest of the phone works.
 */
export default function AppControls({ username, name, rules }: { username: string; name: string; rules: ScreenRules }) {
  const queryClient = useQueryClient();
  const { data: usage } = useQuery({
    queryKey: ['usage', username],
    queryFn: () => api.getUsage(username, 7),
    retry: false,
  });
  const [limitDraft, setLimitDraft] = useState<Record<string, string>>({});

  const apps = useMemo(() => {
    const week = new Map<string, number>();
    for (const day of usage?.days || []) {
      for (const app of day.apps || []) week.set(app.package, (week.get(app.package) || 0) + app.millis);
    }
    for (const pkg of [...(rules.blocked_apps || []), ...Object.keys(rules.app_limits || {})]) {
      if (!week.has(pkg)) week.set(pkg, 0);
    }
    const allowed = new Set(rules.always_allowed || []);
    return [...week.entries()].filter(([pkg]) => !allowed.has(pkg)).sort((a, b) => b[1] - a[1]).slice(0, 25);
  }, [usage, rules]);

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
  const withApps = (blockedApps: string[], appLimits: Record<string, number>): ScreenRules => {
    const rest: ScreenRules = { ...rules };
    delete rest.paused;
    delete rest.paused_until;
    return { ...rest, blocked_apps: blockedApps, app_limits: appLimits };
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

  return (
    <div className="space-y-2" data-testid="app-controls">
      <div className="flex items-center gap-2">
        <AppWindow size={14} className="text-sky-300" />
        <h4 className="text-xs font-semibold text-white">Apps on {name}'s phone</h4>
      </div>
      {apps.length === 0 ? (
        <p className="text-[11px] text-slate-400">No app use reported yet. Apps show up here once {name}'s phone shares its usage.</p>
      ) : (
        <ul className="space-y-1.5">
          {apps.map(([pkg, millis]) => {
            const off = blocked.has(pkg);
            const draft = limitDraft[pkg] ?? (limits[pkg] !== undefined ? String(limits[pkg]) : '');
            return (
              <li key={pkg} className="flex items-center gap-2 text-xs" data-testid={`app-row-${pkg}`}>
                <span className="min-w-0 flex-1">
                  <span className={`block truncate ${off ? 'text-slate-500 line-through' : 'text-slate-200'}`}>{appName(pkg)}</span>
                  <span className="block text-[10px] text-slate-500">{millis ? `${duration(millis)} this week` : 'not used this week'}</span>
                </span>
                <label className="flex items-center gap-1 text-[10px] text-slate-400">
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
                    disabled={off || save.isPending}
                    className="w-16 rounded-lg border border-white/10 bg-white/5 px-1.5 py-1 text-xs text-slate-100"
                  />
                  min/day
                </label>
                <button
                  type="button"
                  role="switch"
                  aria-checked={!off}
                  aria-label={`${appName(pkg)} allowed`}
                  disabled={save.isPending}
                  onClick={() => toggle(pkg)}
                  className={`min-h-11 min-w-14 rounded-lg border px-2 text-[11px] font-semibold ${off ? 'border-red-400/40 bg-red-400/10 text-red-200' : 'border-white/10 bg-white/5 text-slate-200'}`}
                >
                  {off ? 'Off' : 'On'}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {failure && <p className="text-xs text-amber-300">{failure}</p>}
    </div>
  );
}
