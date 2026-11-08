import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, ShieldCheck } from 'lucide-react';
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
 * The parents' side of a minor's notice settings: which kinds of notice this
 * teen may switch off for themselves. Off by default and safe to leave off -
 * a locked kind is simply always on for them, and the safety notices
 * (SOS, low battery, school arrivals) are locked beyond reach: they cannot
 * be allowed here at all.
 */
export default function NotificationLockCard({ username, name, testId = 'guardian-notification-locks-card' }: NotificationLockCardProps) {
  const queryClient = useQueryClient();

  const { data: status, isLoading: statusLoading } = useQuery(statusQuery(username));
  const rules = status?.rules || {};
  const raw = rules.notification_toggle_allowed;
  const allowed = new Set(Array.isArray(raw) ? raw : []);

  // Every category the minor can even see (admin notices are grown-ups'); the
  // safety kind is shown locked rather than offered, so it is clear nothing
  // here can unlock it.
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
    const next = new Set(allowed);
    if (next.has(categoryId)) next.delete(categoryId);
    else next.add(categoryId);
    saveMutation.mutate({ notification_toggle_allowed: [...next] });
  };

  return (
    <MemberCard
      title="Notice settings"
      icon={Bell}
      tone="text-sky-300"
      testId={testId}
      isLoading={statusLoading}
    >
      <div className="space-y-3 text-xs">
        <p className="text-slate-400">
          Choose which notices {name} may switch off for themselves. Anything not switched on here is always on for them.
        </p>

        <div className="flex items-start gap-2 rounded-xl border border-white/10 bg-black/20 p-2.5">
          <ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-400" />
          <div>
            <p className="font-semibold text-white">Family safety notices</p>
            <p className="text-[11px] text-slate-400">
              SOS, low battery, long stops and school arrivals are always on for a minor. This cannot be changed.
            </p>
          </div>
        </div>

        <div className="space-y-1.5">
          {toggleable.map((c) => {
            const on = allowed.has(c.id);
            return (
              <div
                key={c.id}
                className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-black/20 p-2.5"
                data-testid={`notify-lock-row-${c.id}`}
              >
                <div className="min-w-0 pr-2">
                  <p className="font-semibold text-white">{c.label}</p>
                  <p className="text-[10px] text-slate-400">{c.description}</p>
                </div>
                <button
                  type="button"
                  onClick={() => toggle(c.id)}
                  disabled={saveMutation.isPending}
                  aria-pressed={on}
                  aria-label={`${on ? 'Prevent' : 'Let'} ${name} switch off ${c.label}`}
                  data-testid={`toggle-notify-${c.id}`}
                  className={`min-h-7 shrink-0 rounded-lg px-2 py-1 text-[11px] font-medium transition-colors ${
                    on
                      ? 'border border-sky-400/40 bg-sky-400/20 text-sky-200'
                      : 'border border-white/10 bg-white/5 text-slate-400 hover:text-white'
                  }`}
                >
                  {on ? 'Can switch off' : 'Always on'}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </MemberCard>
  );
}
