import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, BellOff, ShieldCheck } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { statusQuery } from '../../lib/memberPage';
import { NOTIFICATION_CATEGORIES } from '../../lib/notificationCategories';
import MemberCard from '../member/MemberCard';
import type { ScreenRules } from '../../types/api';

interface NotificationLockCardProps {
  username: string;
  name: string;
  /** Override when the same card renders more than once on a page (e.g. per device). */
  testId?: string;
}

/**
 * The parents' side of a minor's notice settings: which kinds of notice are
 * enabled or disabled for this non-adult account, and which enabled notices
 * they may switch off for themselves. Safety notices (SOS, low battery,
 * school arrivals) are always on and cannot be disabled or unlocked.
 */
export default function NotificationLockCard({ username, name, testId = 'guardian-notification-locks-card' }: NotificationLockCardProps) {
  const queryClient = useQueryClient();

  const { data: status, isLoading: statusLoading } = useQuery(statusQuery(username));
  const rules = status?.rules || {};
  const rawAllowed = rules.notification_toggle_allowed;
  const rawDisabled = rules.notification_disabled;
  const disabled = new Set(Array.isArray(rawDisabled) ? rawDisabled : []);
  const allowed = new Set((Array.isArray(rawAllowed) ? rawAllowed : []).filter((id) => !disabled.has(id)));

  // Every category the minor can even see (admin notices are grown-ups'); the
  // safety kind is shown locked rather than offered, so it is clear nothing
  // here can disable or unlock it.
  const toggleable = NOTIFICATION_CATEGORIES.filter((c) => !c.adminOnly && c.id !== 'family_safety');

  const saveMutation = useMutation({
    mutationFn: (updated: Partial<ScreenRules>) => {
      const full: ScreenRules = { ...rules, ...updated };
      delete full.paused;
      delete full.paused_until;
      return api.setScreenRules(username, full);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['screen-time', username] });
      void queryClient.invalidateQueries({ queryKey: ['screen-time-rules', username] });
      toast.success('Notification settings updated');
    },
    onError: () => {
      toast.error('Could not save the notification settings');
    },
  });

  const toggle = (categoryId: string) => {
    const nextAllowed = new Set(allowed);
    const nextDisabled = new Set(disabled);
    if (nextAllowed.has(categoryId)) {
      nextAllowed.delete(categoryId);
    } else {
      nextAllowed.add(categoryId);
      nextDisabled.delete(categoryId);
    }
    const patch: Partial<ScreenRules> = { notification_toggle_allowed: [...nextAllowed] };
    if (disabled.size > 0 || nextDisabled.size > 0) {
      patch.notification_disabled = [...nextDisabled];
    }
    saveMutation.mutate(patch);
  };

  const toggleEnabled = (categoryId: string) => {
    const nextDisabled = new Set(disabled);
    const nextAllowed = new Set(allowed);
    if (nextDisabled.has(categoryId)) {
      nextDisabled.delete(categoryId);
    } else {
      nextDisabled.add(categoryId);
      nextAllowed.delete(categoryId);
    }
    const patch: Partial<ScreenRules> = { notification_disabled: [...nextDisabled] };
    if (allowed.has(categoryId)) {
      patch.notification_toggle_allowed = [...nextAllowed];
    }
    saveMutation.mutate(patch);
  };

  const enabledCount = toggleable.length - disabled.size;

  return (
    <MemberCard
      title="Notice settings"
      icon={Bell}
      tone="text-sky-300"
      testId={testId}
      isLoading={statusLoading}
      aside={
        <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 text-[11px] font-medium text-slate-300">
          {enabledCount}/{toggleable.length} enabled
        </span>
      }
    >
      <div className="space-y-3 text-xs">
        <p className="leading-relaxed text-slate-400">
          Set which notifications are enabled or disabled for {name}, and choose which enabled notices {name} may switch off for themselves.
        </p>

        <div className="flex items-start gap-2.5 rounded-xl border border-emerald-400/20 bg-emerald-400/[0.06] p-3">
          <ShieldCheck size={16} className="mt-0.5 shrink-0 text-emerald-300" />
          <div className="space-y-0.5">
            <p className="font-semibold text-white">Family safety notices</p>
            <p className="text-[11px] leading-relaxed text-slate-300">
              SOS, low battery, long stops and school arrivals are always on for a minor. This cannot be changed.
            </p>
          </div>
        </div>

        <div className="space-y-2">
          {toggleable.map((c) => {
            const isDisabled = disabled.has(c.id);
            const on = !isDisabled && allowed.has(c.id);
            return (
              <div
                key={c.id}
                className={`flex flex-col gap-2.5 rounded-xl border p-3 transition-colors sm:flex-row sm:items-center sm:justify-between ${
                  isDisabled
                    ? 'border-white/5 bg-slate-950/40 opacity-80'
                    : 'border-white/10 bg-white/[0.03]'
                }`}
                data-testid={`notify-lock-row-${c.id}`}
              >
                <div className="min-w-0 flex-1 pr-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className={`font-semibold ${isDisabled ? 'text-slate-400 line-through decoration-slate-600' : 'text-white'}`}>
                      {c.label}
                    </p>
                    {isDisabled && (
                      <span className="rounded-full border border-rose-400/30 bg-rose-500/15 px-2 py-0.5 text-[10px] font-semibold text-rose-200">
                        Disabled
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">{c.description}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => toggleEnabled(c.id)}
                    disabled={saveMutation.isPending}
                    aria-pressed={!isDisabled}
                    aria-label={`${isDisabled ? 'Enable' : 'Disable'} ${c.label} for ${name}`}
                    data-testid={`enable-notify-${c.id}`}
                    className={`inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl px-3 text-xs font-semibold transition-colors ${
                      isDisabled
                        ? 'border border-rose-400/35 bg-rose-500/15 text-rose-200 hover:bg-rose-500/25'
                        : 'border border-emerald-400/35 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25'
                    }`}
                  >
                    {isDisabled ? <BellOff size={13} /> : <Bell size={13} />}
                    {isDisabled ? 'Disabled' : 'Enabled'}
                  </button>
                  <button
                    type="button"
                    onClick={() => toggle(c.id)}
                    disabled={saveMutation.isPending}
                    aria-pressed={on}
                    aria-label={`${on ? 'Prevent' : 'Let'} ${name} switch off ${c.label}`}
                    data-testid={`toggle-notify-${c.id}`}
                    className={`min-h-11 shrink-0 rounded-xl px-3 text-xs font-semibold transition-colors ${
                      isDisabled
                        ? 'border border-white/10 bg-white/[0.03] text-slate-400 hover:text-slate-200'
                        : on
                          ? 'border border-sky-400/40 bg-sky-400/20 text-sky-200'
                          : 'border border-white/10 bg-white/5 text-slate-300 hover:text-white'
                    }`}
                  >
                    {isDisabled ? 'Locked off' : on ? 'Can switch off' : 'Always on'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </MemberCard>
  );
}
