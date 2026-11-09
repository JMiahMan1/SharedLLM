import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, BookOpen, CalendarRange, Clock, Globe, MessageSquare, Moon, PlayCircle, Smartphone, Sparkles } from 'lucide-react';
import { api } from '../../services/api';
import { appName, duration } from '../../lib/phoneUsageFormat';
import { formatDayLabel } from '../../lib/healthHub';
import type { ConcernLevel } from '../../types/api';
import { useMemberDeviceNoun } from '../../hooks/useDeviceNoun';

const LEVEL_STYLE: Record<ConcernLevel, string> = {
  severe: 'border-red-400/40 bg-red-500/10 text-red-100',
  high: 'border-amber-400/40 bg-amber-500/10 text-amber-100',
  medium: 'border-white/10 bg-white/[0.04] text-slate-200',
};

/**
 * A child's week for their parents (Phase 11 step 6): built on demand from
 * what the phone and Jarvis reported; anything not reported is left out.
 */
export default function RecapCard({ username, name }: { username: string; name: string }) {
  const device = useMemberDeviceNoun(username);
  const { data, isLoading, isError } = useQuery({
    queryKey: ['guardian-recap', username],
    queryFn: () => api.getGuardianRecap(username),
    retry: false,
  });
  const recap = data?.recap;

  return (
    <section className="glass-panel min-w-0 rounded-2xl border border-white/10 p-4 sm:p-5 space-y-4" data-testid="guardian-recap">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] pb-3">
        <h2 className="flex items-center gap-2.5 text-sm font-semibold tracking-tight text-white">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-sky-400/20 bg-sky-400/10">
            <CalendarRange size={16} className="text-sky-300" />
          </span>
          <span>{name}&apos;s week</span>
        </h2>
        {recap && (
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] font-medium text-slate-300">
            {formatDayLabel(recap.week.start)} - {formatDayLabel(recap.week.end)}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="h-28 skeleton rounded-xl" aria-hidden />
      ) : isError || !recap ? (
        <p className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-xs text-amber-300">The week could not be loaded.</p>
      ) : !recap.has_data ? (
        <p className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-xs text-slate-400" data-testid="guardian-recap-empty">
          Nothing reported from {name}&apos;s {device} yet this week.
        </p>
      ) : (
        <div className="space-y-4">
          {recap.concerns.length > 0 && (
            <ul className="grid grid-cols-1 gap-2 md:grid-cols-2" aria-label="Concerns this week" data-testid="guardian-concerns">
              {recap.concerns.map((c) => (
                <li key={c.category} className={`rounded-xl border p-3 text-xs ${LEVEL_STYLE[c.level]}`}>
                  <span className="flex items-center gap-1.5 font-semibold">
                    <AlertTriangle size={14} className="shrink-0" />
                    <span className="truncate">{c.label}</span>
                    <span className="ml-auto shrink-0 rounded-full bg-black/25 px-2 py-0.5 text-[10px] uppercase tracking-wider opacity-90">
                      {c.level}{c.count > 1 ? ` · ${c.count}×` : ''}
                    </span>
                  </span>
                  {c.latest?.excerpt && <span className="mt-1.5 block rounded-lg bg-black/20 px-2.5 py-1.5 text-slate-200">“{c.latest.excerpt}”</span>}
                  {c.latest?.review?.by === 'model' && c.latest.review.reason && (
                    <span className="mt-1.5 block text-[11px] text-slate-300/90" data-testid="guardian-concern-review">
                      {c.latest.review.verdict === 'doubted' ? 'Local model unsure' : 'Checked by the local model'}: {c.latest.review.reason}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}

          {recap.highlights.length > 0 && (
            <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.06] p-3">
              <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-emerald-300">
                <Sparkles size={13} /> Highlights
              </p>
              <ul className="list-disc space-y-1 pl-5 text-xs text-slate-200" data-testid="guardian-highlights">
                {recap.highlights.map((h) => <li key={h}>{h}</li>)}
              </ul>
            </div>
          )}

          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 text-xs">
            {recap.screen && (
              <div className="rounded-xl border border-white/10 bg-white/[0.04] p-3 space-y-1">
                <p className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-400">
                  <Clock size={12} className="text-sky-300" /> Screen time a day
                </p>
                <p className="text-base font-bold text-white sm:text-lg" data-testid="guardian-screen">
                  {duration(recap.screen.daily_avg_min * 60000)}
                </p>
                {recap.screen.change_pct !== null && (
                  <p className={`text-[11px] font-medium ${recap.screen.change_pct > 15 ? 'text-amber-300' : recap.screen.change_pct < 0 ? 'text-emerald-300' : 'text-slate-400'}`}>
                    {recap.screen.change_pct >= 0 ? '+' : ''}{recap.screen.change_pct}% vs last week
                  </p>
                )}
              </div>
            )}
            {recap.screen && (
              <div className="rounded-xl border border-white/10 bg-white/[0.04] p-3 space-y-1">
                <p className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-400">
                  <Moon size={12} className="text-indigo-300" /> Late nights
                </p>
                <p className="text-base font-bold text-white sm:text-lg">{recap.screen.late_nights}</p>
                <p className="text-[11px] text-slate-400">{device} after 10 pm</p>
              </div>
            )}
            <div className="rounded-xl border border-white/10 bg-white/[0.04] p-3 space-y-1">
              <p className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-400">
                <MessageSquare size={12} className="text-emerald-300" /> Calls &amp; texts
              </p>
              <p className="text-base font-bold text-white sm:text-lg">{recap.comms.calls} · {recap.comms.messages}</p>
              <p className="text-[11px] text-slate-400">with {Math.max(recap.comms.call_contacts, recap.comms.message_contacts)} people</p>
            </div>
            <div className="rounded-xl border border-white/10 bg-white/[0.04] p-3 space-y-1">
              <p className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-400">
                <BookOpen size={12} className="text-amber-300" /> Bible
              </p>
              <p className="text-base font-bold text-white sm:text-lg">
                {recap.faith.bible_days} day{recap.faith.bible_days === 1 ? '' : 's'}
              </p>
              <p className="text-[11px] text-slate-400">this week</p>
            </div>
          </div>

          {(recap.top_apps.length > 0 || recap.media.length > 0 || recap.web.top.length > 0 || recap.web.blocked.length > 0) && (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3 text-xs">
              {recap.top_apps.length > 0 && (
                <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                    <Smartphone size={12} className="text-sky-300" /> Top apps
                  </p>
                  <ul className="space-y-1.5 text-slate-200">
                    {recap.top_apps.map((a) => (
                      <li key={a.package} className="flex items-center justify-between gap-2 rounded-lg bg-white/[0.03] px-2.5 py-1.5">
                        <span className="truncate font-medium">{appName(a.package)}</span>
                        <span className="shrink-0 font-mono text-[11px] text-slate-400">{duration(a.minutes * 60000)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {recap.media.length > 0 && (
                <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                    <PlayCircle size={12} className="text-purple-300" /> Played and watched
                  </p>
                  <ul className="space-y-1.5 text-slate-200">
                    {recap.media.map((m) => (
                      <li key={m.title} className="truncate rounded-lg bg-white/[0.03] px-2.5 py-1.5">
                        {m.title}{m.count > 1 ? ` (${m.count}×)` : ''}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {(recap.web.top.length > 0 || recap.web.blocked.length > 0) && (
                <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                    <Globe size={12} className="text-emerald-300" /> Websites
                  </p>
                  <ul className="space-y-1.5 text-slate-200">
                    {recap.web.top.map((w) => (
                      <li key={w.domain} className="truncate rounded-lg bg-white/[0.03] px-2.5 py-1.5">{w.domain}</li>
                    ))}
                    {recap.web.blocked.map((w) => (
                      <li key={`b-${w.domain}`} className="truncate rounded-lg border border-amber-400/20 bg-amber-400/10 px-2.5 py-1.5 text-amber-200">
                        {w.domain} (blocked {w.count}×)
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
