import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Bell, ChevronDown, ShieldCheck } from 'lucide-react';
import Toggle from '../ui/Toggle';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { statusQuery } from '../../lib/memberPage';
import { NOTIFICATION_CATEGORIES } from '../../lib/notificationCategories';
import MemberCard from '../member/MemberCard';
import type { ScreenRules } from '../../types/api';

const COLLAPSED_ROWS = 4;

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
  // A phone shows the first few; the rest are one tap away (fourteen rows was a dozen screens of switches).
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? toggleable : toggleable.slice(0, COLLAPSED_ROWS);

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
          Choose which notices {name} gets, and which of them {name} may switch off.
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

        <ul className="divide-y divide-white/[0.06] overflow-hidden rounded-xl border border-white/10 bg-white/[0.02]" aria-label={`Notices for ${name}`}>
          {shown.map((c) => {
            const isDisabled = disabled.has(c.id);
            const on = !isDisabled && allowed.has(c.id);
            return (
              <li key={c.id} className="space-y-2 px-3 py-2.5" data-testid={`notify-lock-row-${c.id}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className={`font-semibold ${isDisabled ? 'text-slate-400' : 'text-white'}`}>{c.label}</p>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">{c.description}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 pt-0.5">
                    <span className={`text-[11px] font-medium ${isDisabled ? 'text-rose-300' : 'text-emerald-300'}`} aria-hidden>
                      {isDisabled ? 'Off' : 'On'}
                    </span>
                    <Toggle
                      checked={!isDisabled}
                      onChange={() => toggleEnabled(c.id)}
                      disabled={saveMutation.isPending}
                      ariaLabel={`${c.label} notices for ${name}`}
                      testId={`enable-notify-${c.id}`}
                    />
                  </div>
                </div>
                {/* Only an enabled notice can be left to the child to switch off. */}
                {!isDisabled && (
                  <div className="flex min-h-11 items-center justify-between gap-3 rounded-lg bg-white/[0.03] px-2.5 text-[11px] text-slate-300">
                    <span>Let {name} switch this off</span>
                    <Toggle
                      checked={on}
                      onChange={() => toggle(c.id)}
                      disabled={saveMutation.isPending}
                      ariaLabel={`Let ${name} switch off ${c.label}`}
                      testId={`toggle-notify-${c.id}`}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {toggleable.length > COLLAPSED_ROWS && (
          <button
            type="button"
            onClick={() => setShowAll(!showAll)}
            aria-expanded={showAll}
            className="flex min-h-11 w-full items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.03] text-xs font-semibold text-sky-200 hover:bg-white/[0.06]"
            data-testid="notify-show-all"
          >
            {showAll ? 'Show fewer' : `Show all ${toggleable.length} kinds of notice`}
            <ChevronDown size={14} aria-hidden className={`transition-transform ${showAll ? 'rotate-180' : ''}`} />
          </button>
        )}
      </div>
    </MemberCard>
  );
}
