import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Hourglass, ListChecks, Moon, School, Pause } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { appName, duration } from '../../lib/phoneUsageFormat';
import { earnedLine, howToEarn, lockedLine } from '../../lib/screenTimeText';

/**
 * A member's own screen time (Phase 11 step 4): what is left today, or why the
 * phone is locked and until when, and a way to ask a parent for more.
 *
 * Shown only when a parent has set rules; the numbers are the member's own
 * rules and remaining time, not the collected usage stream.
 */

const ASK_OPTIONS = [15, 30, 60];

export default function ScreenTimeCard() {
  const { user } = useAuth();
  const me = user?.username || '';
  const queryClient = useQueryClient();
  const [note, setNote] = useState('');

  const { data: status } = useQuery({
    queryKey: ['screen-time', me],
    queryFn: () => api.getScreenTimeStatus(me),
    enabled: Boolean(me),
    refetchInterval: 60 * 1000,
    retry: false,
  });
  const { data: pending } = useQuery({
    queryKey: ['screen-time-requests', me],
    queryFn: () => api.getScreenTimeRequests('pending'),
    enabled: Boolean(me),
    retry: false,
  });
  const [askedApps, setAskedApps] = useState<Record<string, boolean>>({});
  const ask = useMutation({
    mutationFn: (minutes: number) => api.askForScreenTime(minutes, note.trim() || undefined),
    onSuccess: () => {
      setNote('');
      void queryClient.invalidateQueries({ queryKey: ['screen-time-requests', me] });
    },
  });
  const askApp = useMutation({
    mutationFn: (pkg: string) => api.askAParent('app_unblock', pkg, note.trim() || undefined, appName(pkg)),
    onSuccess: (_data, pkg) => {
      setAskedApps((prev) => ({ ...prev, [pkg]: true }));
      void queryClient.invalidateQueries({ queryKey: ['guard-requests'] });
    },
  });

  const rules = status?.rules || {};
  const blockedApps = status?.blocked_apps || [];
  const week = status?.week;
  const hasRules = Boolean(
    rules.daily_limit_min !== undefined || rules.bedtime_start || rules.school_start || status?.paused
      || Object.keys(rules.app_limits || {}).length > 0 || blockedApps.length > 0
      || (week?.days_reported || 0) > 0 || Boolean(rules.earn) || Boolean(rules.chores_first),
  );
  if (!status || !hasRules) return null;

  const waiting = (pending?.requests || []).some((r) => r.username === me);
  const failed = ask.error || askApp.error;
  const askError = isAxiosError(failed) ? (failed.response?.data as { detail?: string })?.detail : null;
  const Icon = status.reason === 'bedtime' ? Moon : status.reason === 'school' ? School : status.reason === 'paused' ? Pause
    : status.reason === 'chores' ? ListChecks : Hourglass;
  const earnedText = earnedLine(status);
  const ways = howToEarn(status);
  const waitingChores = status.chores_waiting || [];
  const totalToday = status.limit_min !== null && status.limit_min !== undefined ? status.limit_min + (status.bonus_min || 0) + (status.earned_min || 0) : null;
  const pctLeft = totalToday && status.remaining_min !== null ? Math.max(0, Math.min(100, Math.round((status.remaining_min / Math.max(1, totalToday)) * 100))) : null;

  return (
    <section className="glass-panel rounded-2xl border border-white/10 p-4 sm:p-5 space-y-3" data-testid="screen-time-card">
      <div className="flex items-center justify-between gap-2 border-b border-white/5 pb-3">
        <div className="flex items-center gap-2.5">
          <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border ${status.locked ? 'border-amber-400/30 bg-amber-400/10 text-amber-300' : 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300'}`}>
            <Icon size={16} />
          </div>
          <h3 className="text-sm font-semibold text-white">Screen time</h3>
        </div>
        <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${status.locked ? 'border-amber-400/30 bg-amber-400/10 text-amber-200' : 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200'}`}>
          {status.locked ? 'Locked' : 'Unlocked'}
        </span>
      </div>

      {status.locked ? (
        <p className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-sm text-slate-200" data-testid="screen-time-locked">{lockedLine(status)}</p>
      ) : status.remaining_min !== null ? (
        <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3.5 space-y-2">
          <p className="text-sm text-slate-200" data-testid="screen-time-left">
            <span className="text-base font-bold text-emerald-300">{duration(status.remaining_min * 60000)}</span> left today
            {status.bonus_min > 0 && <span className="text-slate-400"> (with {status.bonus_min} bonus minutes)</span>}
          </p>
          {pctLeft !== null && (
            <div className="h-2 w-full overflow-hidden rounded-full bg-white/10" aria-hidden>
              <div
                className={`h-full rounded-full transition-all ${pctLeft <= 15 ? 'bg-amber-400' : 'bg-emerald-400'}`}
                style={{ width: `${pctLeft}%` }}
              />
            </div>
          )}
        </div>
      ) : (
        <p className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-sm text-slate-300">No daily limit today.</p>
      )}

      {(earnedText || (status.banked_min || 0) > 0 || ways.length > 0 || waitingChores.length > 0) && (
        <div className="space-y-1.5 rounded-xl border border-emerald-400/20 bg-emerald-400/5 p-3.5 text-xs text-slate-300" data-testid="screen-time-earned">
          {earnedText && <p className="font-semibold text-emerald-200">{earnedText}</p>}
          {(status.banked_min || 0) > 0 && (
            <p data-testid="screen-time-banked">
              {status.banked_min} min saved from earlier this week (it starts fresh on Monday).
            </p>
          )}
          {waitingChores.length > 0 && (
            <p className="text-sky-200" data-testid="screen-time-chores-waiting">
              Waiting for a parent's OK: {waitingChores.map((c) => c.title).join(', ')}.
            </p>
          )}
          {ways.length > 0 && <p className="text-slate-400">Earn more: {ways.join('; ')}.</p>}
        </div>
      )}

      {Object.keys(status.app_minutes_left || {}).length > 0 && (
        <ul className="space-y-1 rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs text-slate-300" data-testid="screen-time-apps">
          {Object.entries(status.app_minutes_left || {}).map(([pkg, left]) => (
            <li key={pkg} className="flex items-center justify-between gap-2">
              <span className="font-medium text-slate-200">{appName(pkg)}: </span>
              <span className={left > 0 ? 'text-emerald-300' : 'text-amber-300'}>{left > 0 ? `${duration(left * 60000)} left today` : 'used up for today'}</span>
            </li>
          ))}
        </ul>
      )}

      {blockedApps.length > 0 && (
        <ul className="space-y-2 text-xs text-slate-300" data-testid="screen-time-blocked-apps">
          {blockedApps.map((pkg) => (
            <li key={pkg} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2">
              <span className="font-medium text-slate-200">{appName(pkg)} is off right now</span>
              {askedApps[pkg] ? (
                <span className="rounded-full border border-sky-400/20 bg-sky-400/10 px-2.5 py-1 text-sky-300" data-testid={`asked-unblock-${pkg}`}>Asked a parent</span>
              ) : (
                <button
                  type="button"
                  disabled={askApp.isPending}
                  onClick={() => askApp.mutate(pkg)}
                  data-testid={`ask-unblock-${pkg}`}
                  className="glass-button min-h-11 rounded-xl px-3 text-xs font-medium"
                >
                  Ask a parent
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {week && week.days_reported > 0 && (
        <div className="rounded-xl border border-white/10 bg-white/5 p-3.5 space-y-2 text-xs text-slate-300" data-testid="screen-time-week">
          <p className="font-semibold text-slate-100">
            This week: {duration(week.daily_avg_min * 60000)} a day ({duration(week.total_min * 60000)} across {week.days_reported} {week.days_reported === 1 ? 'day' : 'days'})
          </p>
          {week.top_apps.length > 0 && (
            <p className="text-slate-400" data-testid="screen-time-week-apps">
              Most used: {week.top_apps.map((a) => `${appName(a.package)} (${duration(a.minutes * 60000)})`).join(', ')}
            </p>
          )}
          {week.days.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-0.5" data-testid="screen-time-week-days">
              {week.days.map((d) => (
                <span key={d.day} className="rounded-lg border border-white/5 bg-white/5 px-2.5 py-1 text-[11px] text-slate-300">
                  {d.day.slice(5)}: {duration(d.minutes * 60000)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {(rules.bedtime_start || rules.school_start) && !status.locked && (
        <p className="text-xs text-slate-400">
          {rules.bedtime_start ? `Bedtime ${rules.bedtime_start}-${rules.bedtime_end}. ` : ''}
          {rules.school_start ? `School ${rules.school_start}-${rules.school_end}.` : ''}
        </p>
      )}

      {status.reason !== 'paused' && (
        <div className="space-y-2.5 border-t border-white/5 pt-3">
          {waiting ? (
            <p className="rounded-xl border border-sky-400/20 bg-sky-400/10 p-3 text-xs text-sky-200" data-testid="screen-time-waiting">
              You asked for more time. A parent will answer soon.
            </p>
          ) : (
            <>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={200}
                placeholder="What is it for? (optional)"
                aria-label="What is the extra time for?"
                className="min-h-11 w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:border-emerald-400/50 focus:outline-none"
              />
              <div className="grid grid-cols-3 gap-2">
                {ASK_OPTIONS.map((minutes) => (
                  <button
                    key={minutes}
                    type="button"
                    disabled={ask.isPending}
                    onClick={() => ask.mutate(minutes)}
                    className="glass-button min-h-11 rounded-xl px-2.5 py-1.5 text-xs font-medium"
                  >
                    Ask for {minutes} min
                  </button>
                ))}
              </div>
            </>
          )}
          {askError && <p className="text-xs text-amber-300">{askError}</p>}
        </div>
      )}
    </section>
  );
}
