import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Hourglass, Moon, School, Pause } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { appName, duration } from '../../lib/phoneUsageFormat';
import { lockedLine } from '../../lib/screenTimeText';

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
  const ask = useMutation({
    mutationFn: (minutes: number) => api.askForScreenTime(minutes, note.trim() || undefined),
    onSuccess: () => {
      setNote('');
      void queryClient.invalidateQueries({ queryKey: ['screen-time-requests', me] });
    },
  });

  const rules = status?.rules || {};
  const hasRules = Boolean(
    rules.daily_limit_min !== undefined || rules.bedtime_start || rules.school_start || status?.paused
      || Object.keys(rules.app_limits || {}).length > 0,
  );
  if (!status || !hasRules) return null;

  const waiting = (pending?.requests || []).some((r) => r.username === me);
  const askError = isAxiosError(ask.error) ? (ask.error.response?.data as { detail?: string })?.detail : null;
  const Icon = status.reason === 'bedtime' ? Moon : status.reason === 'school' ? School : status.reason === 'paused' ? Pause : Hourglass;

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5" data-testid="screen-time-card">
      <div className="flex items-center gap-2">
        <Icon size={16} className={status.locked ? 'text-amber-300' : 'text-emerald-300'} />
        <h3 className="text-sm font-semibold text-white">Screen time</h3>
      </div>

      {status.locked ? (
        <p className="mt-2 text-sm text-slate-200" data-testid="screen-time-locked">{lockedLine(status)}</p>
      ) : status.remaining_min !== null ? (
        <p className="mt-2 text-sm text-slate-200" data-testid="screen-time-left">
          <span className="font-semibold text-emerald-300">{duration(status.remaining_min * 60000)}</span> left today
          {status.bonus_min > 0 && <span className="text-slate-400"> (with {status.bonus_min} bonus minutes)</span>}
        </p>
      ) : (
        <p className="mt-2 text-sm text-slate-300">No daily limit today.</p>
      )}

      {Object.keys(status.app_minutes_left || {}).length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-slate-300" data-testid="screen-time-apps">
          {Object.entries(status.app_minutes_left || {}).map(([pkg, left]) => (
            <li key={pkg}>
              {appName(pkg)}: {left > 0 ? `${duration(left * 60000)} left today` : 'used up for today'}
            </li>
          ))}
        </ul>
      )}

      {(rules.bedtime_start || rules.school_start) && !status.locked && (
        <p className="mt-1 text-xs text-slate-400">
          {rules.bedtime_start ? `Bedtime ${rules.bedtime_start}-${rules.bedtime_end}. ` : ''}
          {rules.school_start ? `School ${rules.school_start}-${rules.school_end}.` : ''}
        </p>
      )}

      {status.reason !== 'paused' && (
        <div className="mt-3 space-y-2">
          {waiting ? (
            <p className="text-xs text-sky-300" data-testid="screen-time-waiting">You asked for more time. A parent will answer soon.</p>
          ) : (
            <>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={200}
                placeholder="What is it for? (optional)"
                aria-label="What is the extra time for?"
                className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500"
              />
              <div className="flex flex-wrap gap-2">
                {ASK_OPTIONS.map((minutes) => (
                  <button
                    key={minutes}
                    type="button"
                    disabled={ask.isPending}
                    onClick={() => ask.mutate(minutes)}
                    className="glass-button px-3 py-1.5 text-xs min-h-11"
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
