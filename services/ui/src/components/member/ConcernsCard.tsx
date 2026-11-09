import { useQuery } from '@tanstack/react-query';
import { AlertOctagon, AlertTriangle, Info, ShieldAlert } from 'lucide-react';
import { api } from '../../services/api';
import { relativeTime } from '../../lib/wanderTrips';
import MemberCard from './MemberCard';
import type { ConcernLevel } from '../../types/api';
import { useMemberDeviceNoun } from '../../hooks/useDeviceNoun';

const LEVEL: Record<ConcernLevel, { word: string; icon: typeof AlertTriangle; style: string }> = {
  severe: { word: 'Severe', icon: AlertOctagon, style: 'border-red-400/40 bg-red-400/10 text-red-200' },
  high: { word: 'High', icon: AlertTriangle, style: 'border-amber-400/40 bg-amber-400/10 text-amber-200' },
  medium: { word: 'Worth a look', icon: Info, style: 'border-white/10 bg-white/5 text-slate-300' },
};

const WHERE: Record<string, string> = {
  sms: 'a text message',
  web: 'a website',
  media: 'something played',
  media_scan: 'a photo or video',
  page: 'an app',
  calls: 'a call',
};

function whereText(feature?: string, action?: string): string {
  if (feature === 'media' && (action?.includes('photo') || action?.includes('image') || action?.includes('video') || action?.includes('scan'))) {
    return 'a photo or video';
  }
  return WHERE[feature || ''] || 'their activity';
}

/**
 * Bark-style concern cards: what content monitoring found, how serious it is,
 * the words that raised it (in context), and a way to start talking about it
 * from the week's guidance. Concerns the local model dismissed in context
 * say so rather than disappearing.
 */
export default function ConcernsCard({ username, name, limit = 5 }: { username: string; name: string; limit?: number }) {
  const device = useMemberDeviceNoun(username);
  const concerns = useQuery({ queryKey: ['guardian-concerns', username], queryFn: () => api.getGuardianConcerns(username, 50), retry: false });
  const guidance = useQuery({ queryKey: ['guardian-guidance', username], queryFn: () => api.getGuardianGuidance(username), retry: false });
  const starters = new Map((guidance.data?.prompts || []).map((p) => [p.signal, p.starter]));
  const list = (concerns.data?.concerns || []).slice(0, limit);

  return (
    <MemberCard
      title="Concerns"
      icon={ShieldAlert}
      tone="text-red-300"
      testId="member-concerns"
      isLoading={concerns.isLoading}
      error={concerns.isError ? 'Concerns could not be loaded.' : null}
      empty={list.length === 0 ? `Nothing of concern found on ${name}'s ${device}.` : null}
      aside={concerns.data && concerns.data.concerns.length > limit ? `${limit} of ${concerns.data.concerns.length}` : undefined}
    >
      <ul className="space-y-2.5">
        {list.map((c, i) => {
          const level = LEVEL[c.level] || LEVEL.medium;
          const Icon = level.icon;
          const dismissed = c.review?.verdict === 'dismissed';
          return (
            <li key={`${c.category}-${c.at}-${i}`} className={`rounded-xl border p-3.5 text-xs space-y-2 ${level.style}`} data-testid="concern">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 font-semibold">
                  <Icon size={14} className="shrink-0" /> {c.label} · {level.word}
                </p>
                {c.at && <span className="rounded-full bg-black/20 px-2 py-0.5 font-normal text-[10px] opacity-80">{relativeTime(c.at)}</span>}
              </div>
              <p className="rounded-lg bg-black/20 px-2.5 py-2 leading-relaxed opacity-95">
                In {whereText(c.feature, c.action)}: “{c.excerpt}”
              </p>
              {c.review?.reason && (
                <p className="text-[11px] leading-relaxed opacity-85">
                  {dismissed ? <span className="font-semibold text-emerald-300">Probably fine: </span> : ''}
                  {c.review.reason}
                </p>
              )}
              {starters.get(c.category) && (
                <div className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-2 text-slate-200">
                  <span className="font-semibold text-sky-200">What to say: </span>
                  {starters.get(c.category)}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </MemberCard>
  );
}
