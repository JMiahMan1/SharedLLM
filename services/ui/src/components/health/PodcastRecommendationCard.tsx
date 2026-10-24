import { useEffect, useState } from 'react';
import { Mic, Sparkles, ArrowRight } from 'lucide-react';
import { api } from '../../services/api';

type Rec = {
  theme: string;
  show: string | null;
  host: string | null;
  style: string;
  reason: string;
  matched_on: string;
  kind: string;
  redeemed: boolean;
  redirect_to?: string;
} | null;

/**
 * A podcast suggestion for the signed-in member, derived from their listening.
 *
 * Themes are first redeemed (a wholesome show with the same kind of content)
 * and, where no redeemed equivalent exists, redirected to a different wholesome
 * activity. Suggestions are the member's to see; the raw feed behind them stays
 * parents-only. Quiet when there is nothing to suggest yet.
 */
export default function PodcastRecommendationCard() {
  const [rec, setRec] = useState<Rec>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await api.getPodcastRecommendation();
        if (alive) setRec(data?.recommendation ?? null);
      } catch {
        if (alive) setRec(null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (loading) return null;
  if (!rec) return null;

  const redeemed = rec.redeemed && !!rec.show;

  return (
    <div
      data-testid="podcast-recommendation-card"
      className="glass-panel rounded-2xl border border-amber-500/20 bg-amber-500/5 p-4 sm:p-5"
    >
      <div className="flex items-start gap-3">
        <div className="shrink-0 rounded-xl border border-amber-500/30 bg-amber-500/10 p-2.5">
          <Mic size={20} className="text-amber-400" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Sparkles size={14} className="text-amber-400" />
            <h3 className="text-sm font-semibold text-white">
              {redeemed ? 'A podcast for you' : 'Try something else'}
            </h3>
          </div>
          {redeemed ? (
            <p className="mt-1 text-sm text-slate-200">
              Try <span className="font-semibold text-amber-300">{rec.show}</span>
              {rec.host ? ` (${rec.host})` : ''} — {rec.style}.
            </p>
          ) : (
            <p className="mt-1 text-sm text-slate-200">
              How about <span className="font-semibold text-amber-300">{rec.redirect_to}</span>{' '}
              instead?
            </p>
          )}
          <p className="mt-1 text-xs text-slate-400">{rec.reason}</p>
          <p className="mt-2 flex items-center gap-1 text-[11px] text-slate-500">
            <ArrowRight size={10} />
            Because you&rsquo;ve been listening to {rec.matched_on}.
          </p>
        </div>
      </div>
    </div>
  );
}
