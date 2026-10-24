import { useQuery } from '@tanstack/react-query';
import { Activity, BookOpen, Gamepad2, Globe, MessageSquare, Music, Phone, Smartphone } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from '../../services/api';

const ICONS: Record<string, LucideIcon> = {
  media: Music, music: Music, video: Music, podcast: Music, audiobook: Music,
  bible: BookOpen, trivia: Gamepad2, game: Gamepad2, web: Globe, sms: MessageSquare, calls: Phone, page: Smartphone,
};

function when(at: string): string {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? at : d.toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
}

/** The child's recent activity, newest first (parents only, like the rest of the feed). */
export default function ActivityFeedCard({ username, name }: { username: string; name: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['guardian-activity', username],
    queryFn: () => api.getGuardianActivity(username, 60),
    retry: false,
  });
  const events = data?.events ?? [];

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-2" data-testid="guardian-activity">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
        <Activity size={16} className="text-emerald-300" /> What {name} has been doing
      </h2>
      {isLoading ? (
        <div className="h-16 skeleton rounded-xl" aria-hidden />
      ) : isError ? (
        <p className="text-xs text-amber-300">Activity could not be loaded.</p>
      ) : events.length === 0 ? (
        <p className="text-xs text-slate-500">Nothing recorded yet.</p>
      ) : (
        <ul className="max-h-80 overflow-y-auto divide-y divide-white/5">
          {events.map((e, i) => {
            const Icon = ICONS[e.feature] || Activity;
            const body = e.feature === 'sms' && typeof e.detail?.body === 'string' ? e.detail.body : null;
            return (
              <li key={`${e.at}-${i}`} className="flex items-start gap-2 py-1.5 text-xs">
                <Icon size={13} className="mt-0.5 shrink-0 text-slate-400" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-slate-200">
                    {e.item || e.feature}
                    {e.action && e.action !== 'view' ? <span className="text-slate-500"> · {e.action}</span> : null}
                  </span>
                  {body && <span className="block text-slate-400 line-clamp-2">{body}</span>}
                </span>
                <span className="shrink-0 text-[10px] text-slate-500">{when(e.at)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
