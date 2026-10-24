import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CalendarRange } from 'lucide-react';
import { api } from '../../services/api';
import { appName, duration } from '../../lib/phoneUsageFormat';
import { formatDayLabel } from '../../lib/healthHub';
import type { ConcernLevel } from '../../types/api';

const LEVEL_STYLE: Record<ConcernLevel, string> = {
  severe: 'border-red-400/40 bg-red-400/10 text-red-200',
  high: 'border-amber-400/40 bg-amber-400/10 text-amber-200',
  medium: 'border-white/10 bg-white/5 text-slate-300',
};

/**
 * A child's week for their parents (Phase 11 step 6): built on demand from
 * what the phone and Jarvis reported; anything not reported is left out.
 */
export default function RecapCard({ username, name }: { username: string; name: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['guardian-recap', username],
    queryFn: () => api.getGuardianRecap(username),
    retry: false,
  });
  const recap = data?.recap;

  return (
    <section className="glass-panel rounded-2xl p-4 sm:p-5 space-y-3" data-testid="guardian-recap">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-white">
          <CalendarRange size={16} className="text-sky-300" /> {name}'s week
        </h2>
        {recap && <span className="text-[10px] text-slate-500">{formatDayLabel(recap.week.start)} – {formatDayLabel(recap.week.end)}</span>}
      </div>

      {isLoading ? (
        <div className="h-24 skeleton rounded-xl" aria-hidden />
      ) : isError || !recap ? (
        <p className="text-xs text-amber-300">The week could not be loaded.</p>
      ) : !recap.has_data ? (
        <p className="text-xs text-slate-400" data-testid="guardian-recap-empty">
          Nothing reported from {name}'s phone yet this week.
        </p>
      ) : (
        <>
          {recap.concerns.length > 0 && (
            <ul className="space-y-1.5" aria-label="Concerns this week" data-testid="guardian-concerns">
              {recap.concerns.map((c) => (
                <li key={c.category} className={`rounded-xl border p-2.5 text-xs ${LEVEL_STYLE[c.level]}`}>
                  <span className="flex items-center gap-1.5 font-semibold">
                    <AlertTriangle size={13} /> {c.label}
                    <span className="ml-auto text-[10px] uppercase tracking-wider opacity-80">{c.level}{c.count > 1 ? ` · ${c.count}×` : ''}</span>
                  </span>
                  {c.latest?.excerpt && <span className="mt-1 block text-slate-300">“{c.latest.excerpt}”</span>}
                  {c.latest?.review?.by === 'model' && c.latest.review.reason && (
                    <span className="mt-1 block text-[10px] text-slate-400" data-testid="guardian-concern-review">
                      {c.latest.review.verdict === 'doubted' ? 'Local model unsure' : 'Checked by the local model'}: {c.latest.review.reason}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}

          {recap.highlights.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-slate-300" data-testid="guardian-highlights">
              {recap.highlights.map((h) => <li key={h}>{h}</li>)}
            </ul>
          )}

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-xs">
            {recap.screen && (
              <div className="rounded-xl border border-white/5 bg-white/[0.03] p-2.5">
                <p className="text-[10px] uppercase tracking-wider text-slate-500">Phone a day</p>
                <p className="font-semibold text-slate-100" data-testid="guardian-screen">{duration(recap.screen.daily_avg_min * 60000)}</p>
                {recap.screen.change_pct !== null && (
                  <p className="text-[10px] text-slate-400">{recap.screen.change_pct >= 0 ? '+' : ''}{recap.screen.change_pct}% vs last week</p>
                )}
              </div>
            )}
            {recap.screen && (
              <div className="rounded-xl border border-white/5 bg-white/[0.03] p-2.5">
                <p className="text-[10px] uppercase tracking-wider text-slate-500">Late nights</p>
                <p className="font-semibold text-slate-100">{recap.screen.late_nights}</p>
                <p className="text-[10px] text-slate-400">phone after 10 pm</p>
              </div>
            )}
            <div className="rounded-xl border border-white/5 bg-white/[0.03] p-2.5">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">Calls & texts</p>
              <p className="font-semibold text-slate-100">{recap.comms.calls} · {recap.comms.messages}</p>
              <p className="text-[10px] text-slate-400">with {Math.max(recap.comms.call_contacts, recap.comms.message_contacts)} people</p>
            </div>
            <div className="rounded-xl border border-white/5 bg-white/[0.03] p-2.5">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">Bible</p>
              <p className="font-semibold text-slate-100">{recap.faith.bible_days} day{recap.faith.bible_days === 1 ? '' : 's'}</p>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-3 text-xs">
            {recap.top_apps.length > 0 && (
              <div>
                <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Top apps</p>
                <ul className="space-y-0.5 text-slate-300">
                  {recap.top_apps.map((a) => <li key={a.package} className="flex justify-between gap-2"><span className="truncate">{appName(a.package)}</span><span className="text-slate-500">{duration(a.minutes * 60000)}</span></li>)}
                </ul>
              </div>
            )}
            {recap.media.length > 0 && (
              <div>
                <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Played and watched</p>
                <ul className="space-y-0.5 text-slate-300">
                  {recap.media.map((m) => <li key={m.title} className="truncate">{m.title}{m.count > 1 ? ` (${m.count}×)` : ''}</li>)}
                </ul>
              </div>
            )}
            {(recap.web.top.length > 0 || recap.web.blocked.length > 0) && (
              <div>
                <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Websites</p>
                <ul className="space-y-0.5 text-slate-300">
                  {recap.web.top.map((w) => <li key={w.domain} className="truncate">{w.domain}</li>)}
                  {recap.web.blocked.map((w) => <li key={`b-${w.domain}`} className="truncate text-amber-300">{w.domain} (blocked {w.count}×)</li>)}
                </ul>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
