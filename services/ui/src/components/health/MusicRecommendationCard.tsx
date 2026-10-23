import { useEffect, useState } from 'react';
import { Music2, Sparkles } from 'lucide-react';
import { api } from '../../services/api';

type Rec = {
  lane: string;
  artist: string;
  style: string;
  reason: string;
  matched_on: string;
  kind: string;
} | null;

/**
 * A music suggestion for the signed-in member, derived from their listening.
 *
 * Suggestions are the member's to see; the raw feed behind them stays
 * parents-only. Quiet when there is nothing to suggest yet.
 */
export default function MusicRecommendationCard() {
  const [rec, setRec] = useState<Rec>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await api.getMusicRecommendation();
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

  return (
    <div
      data-testid="music-recommendation-card"
      className="glass-panel rounded-2xl border border-emerald-500/20 bg-emerald-500/5 p-4 sm:p-5"
    >
      <div className="flex items-start gap-3">
        <div className="shrink-0 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-2.5">
          <Music2 size={20} className="text-emerald-400" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Sparkles size={14} className="text-emerald-400" />
            <h3 className="text-sm font-semibold text-white">A song for you</h3>
          </div>
          <p className="mt-1 text-sm text-slate-200">
            Try <span className="font-semibold text-emerald-300">{rec.artist}</span> —{' '}
            {rec.style}.
          </p>
          <p className="mt-1 text-xs text-slate-400">{rec.reason}</p>
          <p className="mt-2 text-[11px] text-slate-500">
            Because you&rsquo;ve been listening to {rec.matched_on}.
          </p>
        </div>
      </div>
    </div>
  );
}