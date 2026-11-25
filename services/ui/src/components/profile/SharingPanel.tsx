import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Eye, ChevronRight } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { Toggle } from '../ui/Toggle';
import { SHARE_AREA_TEXT, type ShareArea, type SharingState } from '../../lib/profileStats';

const SCOPE_WORDS: Record<string, string> = {
  totals: 'steps and distance', workouts: 'workouts', achievements: 'achievements', bible: 'Bible reading and quiz scores',
};
const HEALTH_WORDS: Record<string, string> = {
  sleep: 'sleep', nutrition: 'meals', cycle: 'cycle', fasting: 'fasting', digital: 'screen time',
};

/**
 * "What your family sees" on your own profile. The areas with no switch of
 * their own are turned on here; the areas that already have one (activity,
 * Wander and Bible in Settings, health in Health) are shown with their state
 * and a link to that switch, never a second one.
 */
export default function SharingPanel({ sharing }: { sharing: SharingState | null | undefined }) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<ShareArea | null>(null);
  const save = useMutation({
    mutationFn: (areas: ShareArea[]) => api.setProfileSharing(areas),
    onSuccess: (next) => {
      queryClient.setQueryData(['profile-stats', 'me'], (old: unknown) =>
        old && typeof old === 'object' ? { ...(old as object), sharing: next } : old);
      void queryClient.invalidateQueries({ queryKey: ['profile-stats'] });
      toast.success('Saved');
    },
    onError: () => toast.error('Could not save that just now'),
    onSettled: () => setPending(null),
  });

  if (!sharing) {
    return (
      <section className="glass-panel rounded-2xl border border-white/10 p-4 text-sm text-amber-300" data-testid="profile-sharing">
        Your sharing choices could not be read just now.
      </section>
    );
  }
  const on = new Set(sharing.areas ?? []);
  const toggle = (area: ShareArea, next: boolean) => {
    const areas = sharing.available.filter((a) => (a === area ? next : on.has(a)));
    setPending(area);
    save.mutate(areas);
  };
  const activity = sharing.activity_sharing;
  const activityText = !activity
    ? 'Could not be read just now.'
    : activity.enabled && activity.share.length
      ? `On: ${activity.share.map((s) => SCOPE_WORDS[s] ?? s).join(', ')}${activity.audience === 'users' ? ' (chosen people)' : ''}.`
      : 'Off.';
  const health = sharing.health_shares;
  const healthText = !health
    ? 'Could not be read just now.'
    : health.length
      ? `Shared: ${[...new Set(health.map((h) => HEALTH_WORDS[h.scope] ?? h.scope))].join(', ')} with ${new Set(health.map((h) => h.viewer)).size} ${new Set(health.map((h) => h.viewer)).size === 1 ? 'person' : 'people'}.`
      : 'Off.';

  return (
    <section className="glass-panel space-y-3 rounded-2xl border border-white/10 p-4 sm:p-5" data-testid="profile-sharing" aria-labelledby="profile-sharing-title">
      <div className="flex items-center gap-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04]"><Eye size={16} className="text-purple-300" aria-hidden /></span>
        <div>
          <h2 id="profile-sharing-title" className="text-sm font-semibold text-white">What your family sees</h2>
          <p className="text-[11px] text-slate-400">You always see all of your own. Each area below is yours to share.</p>
        </div>
      </div>
      <ul className="divide-y divide-white/5">
        {sharing.available.map((area) => (
          <li key={area} className="flex min-h-11 items-center gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-100">{SHARE_AREA_TEXT[area].title}</p>
              <p className="text-[11px] text-slate-400">{SHARE_AREA_TEXT[area].detail}</p>
            </div>
            <Toggle
              checked={on.has(area)}
              disabled={pending !== null || sharing.areas === null}
              onChange={(next) => toggle(area, next)}
              ariaLabel={`Share ${SHARE_AREA_TEXT[area].title} with the family`}
              testId={`profile-share-${area}`}
            />
          </li>
        ))}
        <ExistingSwitch title="Activity, Wander and Bible" text={activityText} to="/settings#settings-family" testId="profile-share-activity" />
        <ExistingSwitch title="Health" text={healthText} to="/fitness?tab=health" testId="profile-share-health" />
        <li className="flex min-h-11 items-center gap-3 py-2.5">
          <div className="min-w-0 flex-1">
            <p className="text-sm text-slate-100">Games</p>
            <p className="text-[11px] text-slate-400">Arcade scores are on the family scoreboard already.</p>
          </div>
        </li>
      </ul>
    </section>
  );
}

function ExistingSwitch({ title, text, to, testId }: { title: string; text: string; to: string; testId: string }) {
  return (
    <li>
      <Link to={to} data-testid={testId}
        className="group flex min-h-11 items-center gap-3 rounded-lg py-2.5 hover:bg-white/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/70">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-slate-100">{title}</p>
          <p className="text-[11px] text-slate-400">{text} Change it where it is set.</p>
        </div>
        <ChevronRight size={16} aria-hidden className="shrink-0 text-slate-500 group-hover:text-slate-300" />
      </Link>
    </li>
  );
}
