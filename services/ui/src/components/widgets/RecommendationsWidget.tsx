import { useQueries } from '@tanstack/react-query';
import { BookOpen, Film, Footprints, Gamepad2, Mic, Music2, RefreshCw, Sparkles, type LucideIcon } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { IActiveMediaWidgetProps } from '../../types/widget';

/**
 * "For you" on the dashboard: the member's own suggestions, derived from what
 * they listen to, watch and read.
 *
 * Suggestions are the member's to see; the raw activity behind them stays
 * parents-only, so this card only ever shows the derived suggestion and the
 * one-line "because". Each kind is its own request: one service failing does
 * not hide the others, and nothing is shown that the server did not return.
 */

interface Suggestion {
  kind: string;
  icon: LucideIcon;
  tint: string;
  /** The suggestion itself, or the redirect when no redeemed match exists. */
  pick: string;
  detail: string;
  reason: string;
  because: string;
}

type Redeemable = { redeemed: boolean; redirect_to?: string; style: string; reason: string; matched_on: string };

function redeemOrRedirect(rec: Redeemable, name: string | null, by: string | null): Pick<Suggestion, 'pick' | 'detail'> {
  if (rec.redeemed && name) return { pick: by ? `${name} · ${by}` : name, detail: rec.style };
  return { pick: rec.redirect_to || 'Something different', detail: 'Something different to try instead' };
}

const SOURCES = [
  {
    kind: 'music',
    load: async (): Promise<Suggestion | null> => {
      const rec = (await api.getMusicRecommendation()).recommendation;
      if (!rec) return null;
      return {
        kind: 'music', icon: Music2, tint: 'text-emerald-300',
        pick: rec.artist, detail: rec.style, reason: rec.reason,
        because: `Because you've been listening to ${rec.matched_on}`,
      };
    },
  },
  {
    kind: 'video',
    load: async (): Promise<Suggestion | null> => {
      const rec = (await api.getVideoRecommendation()).recommendation;
      if (!rec) return null;
      return {
        kind: 'video', icon: Film, tint: 'text-violet-300', ...redeemOrRedirect(rec, rec.creator, null),
        reason: rec.reason, because: `Because you've been watching ${rec.matched_on}`,
      };
    },
  },
  {
    kind: 'podcast',
    load: async (): Promise<Suggestion | null> => {
      const rec = (await api.getPodcastRecommendation()).recommendation;
      if (!rec) return null;
      return {
        kind: 'podcast', icon: Mic, tint: 'text-amber-300', ...redeemOrRedirect(rec, rec.show, rec.host),
        reason: rec.reason, because: `Because you've been listening to ${rec.matched_on}`,
      };
    },
  },
  {
    kind: 'book',
    load: async (): Promise<Suggestion | null> => {
      const rec = (await api.getBookRecommendation()).recommendation;
      if (!rec) return null;
      return {
        kind: 'book', icon: BookOpen, tint: 'text-sky-300', ...redeemOrRedirect(rec, rec.title, rec.author),
        reason: rec.reason, because: `Because you've been reading ${rec.matched_on}`,
      };
    },
  },
  {
    kind: 'game',
    load: async (): Promise<Suggestion | null> => {
      const rec = (await api.getGameRecommendation()).recommendation;
      if (!rec) return null;
      return {
        kind: 'game', icon: Gamepad2, tint: 'text-fuchsia-300', ...redeemOrRedirect(rec, rec.game, null),
        reason: rec.reason, because: `Because you've been playing ${rec.matched_on}`,
      };
    },
  },
  {
    kind: 'activity',
    load: async (): Promise<Suggestion | null> => {
      const rec = (await api.getActivityRecommendation()).recommendation;
      if (!rec) return null;
      return {
        kind: 'activity', icon: Footprints, tint: 'text-lime-300',
        pick: rec.activity, detail: rec.style, reason: rec.reason,
        because: `Because of ${rec.matched_on}`,
      };
    },
  },
] as const;

export default function RecommendationsWidget({ settingsButton, userSettings }: IActiveMediaWidgetProps) {
  const { trigger } = useHaptics();
  const roomy = userSettings.size !== 'small';

  const results = useQueries({
    queries: SOURCES.map((source) => ({
      queryKey: ['recommendation', source.kind],
      queryFn: source.load,
      // Suggestions follow the week's activity, not the minute's.
      staleTime: 10 * 60 * 1000,
      retry: 1,
    })),
  });

  const loading = results.some((r) => r.isLoading);
  const failed = results.filter((r) => r.isError);
  const suggestions = results.map((r) => r.data).filter((s): s is Suggestion => !!s);
  const retry = () => {
    void trigger('light');
    failed.forEach((r) => void r.refetch());
  };

  return (
    <div className="glass-panel h-full flex flex-col rounded-2xl p-4 overflow-hidden" data-testid="recommendations-widget">
      <div className="flex items-center justify-between gap-2 shrink-0 min-h-11">
        <div className="flex items-center gap-2 min-w-0">
          <Sparkles size={16} className="text-emerald-300 shrink-0" />
          <h3 className="text-sm font-bold text-white truncate">For you</h3>
        </div>
        {settingsButton}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
        {loading ? (
          <div className="space-y-2.5 py-1">
            <div className="h-3 w-24 skeleton rounded" />
            <div className="h-3 w-full skeleton rounded" />
            <div className="h-3 w-4/5 skeleton rounded" />
          </div>
        ) : failed.length === SOURCES.length ? (
          <div className="h-full flex flex-col items-center justify-center text-center gap-2 py-3" data-testid="recommendations-error">
            <p className="text-xs text-amber-300/90 leading-snug">Suggestions could not load.</p>
            <button
              type="button"
              onClick={retry}
              className="glass-button px-3 py-1.5 text-xs inline-flex items-center gap-1.5 min-h-11 pointer-coarse:min-h-11"
            >
              <RefreshCw size={12} />
              Try again
            </button>
          </div>
        ) : suggestions.length === 0 ? (
          <div className="h-full flex items-center justify-center text-center py-3" data-testid="recommendations-empty">
            <p className="text-xs text-slate-500 leading-snug">
              No suggestions yet. They appear here from what you listen to, watch, read and play.
            </p>
          </div>
        ) : (
          <ul className="space-y-3 py-1">
            {suggestions.map((s) => (
              <li key={s.kind} className="flex items-start gap-2.5" data-testid={`recommendation-${s.kind}`}>
                <s.icon size={15} className={`${s.tint} shrink-0 mt-0.5`} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-slate-100 truncate">{s.pick}</p>
                  <p className="text-xs text-slate-400 truncate">{s.detail}</p>
                  {roomy && <p className="mt-0.5 text-[11px] text-slate-500 leading-snug">{s.reason}</p>}
                  <p className="mt-0.5 text-[11px] text-slate-600 truncate">{s.because}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {!loading && failed.length > 0 && failed.length < SOURCES.length && (
        <button
          type="button"
          onClick={retry}
          className="shrink-0 mt-2 text-[11px] text-amber-300/80 inline-flex items-center gap-1 min-h-11 pointer-coarse:min-h-11"
          data-testid="recommendations-partial-error"
        >
          <RefreshCw size={11} />
          Some suggestions could not load. Try again
        </button>
      )}
    </div>
  );
}
