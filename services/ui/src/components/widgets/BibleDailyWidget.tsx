import { useQuery } from '@tanstack/react-query';
import { BookOpen, ExternalLink, Flame, RefreshCw, Sparkles } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';
import type { IActiveMediaWidgetProps } from '../../types/widget';
import type { BibleDailyResponse, BibleDevotionalEntry, BibleVerseOfDay } from '../../types/api';

/**
 * Verse of the Day on the family dashboard.
 *
 * One `/api/bible/daily` call fills the whole card -- verse, devotional, streaks
 * -- so the dashboard and the Android home screen cannot disagree about what
 * today is, and so opening the dashboard costs one request rather than four.
 *
 * Sizing: the grid hands this widget a ~280px column and 200px rows, so nothing
 * here is sized in pixels. `small` drops the devotional and the streak to keep
 * the verse itself readable, `medium` shows both, and anything taller scrolls.
 */

function verseOf(day: BibleDailyResponse['verse_of_day']): BibleVerseOfDay | null {
  return 'error' in day ? null : day;
}

function devotionalLine(entry: BibleDevotionalEntry | null | undefined): string | null {
  if (!entry) return null;
  return entry.kind === 'link' ? entry.title || entry.work : entry.title || entry.text.slice(0, 90);
}

export default function BibleDailyWidget({ settingsButton, userSettings }: IActiveMediaWidgetProps) {
  const navigate = useNavigate();
  const { trigger } = useHaptics();
  const size = userSettings.size;
  const roomy = size !== 'small';

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['bible-daily'],
    queryFn: () => api.getBibleDaily(),
    // The verse is date-derived, so it cannot change until tomorrow. A stale
    // answer is still today's verse; only a hard failure needs a retry.
    staleTime: 30 * 60 * 1000,
    retry: 1,
  });

  const openReader = (ref?: string) => {
    void trigger('light');
    navigate(ref ? `/bible?ref=${encodeURIComponent(ref)}` : '/bible');
  };

  const openDevotional = async (entry: BibleDevotionalEntry) => {
    void trigger('light');
    if (entry.kind === 'link' && entry.url) {
      window.open(entry.url, '_blank', 'noopener,noreferrer');
      return;
    }
    navigate('/bible?tab=devotional');
  };

  // A corpus that was never imported is a configuration problem the reader has
  // to hear about. The server's own wording names the fix, so show it rather
  // than an empty card that looks like "no verse today".
  const failure = error instanceof Error ? error.message : error ? String(error) : null;
  const verse = data ? verseOf(data.verse_of_day) : null;
  const verseError = data && 'error' in data.verse_of_day ? data.verse_of_day.error : null;
  const devotional = devotionalLine(data?.devotional?.entry);

  return (
    <div className="glass-panel h-full flex flex-col rounded-2xl p-4 overflow-hidden">
      <div className="flex items-center justify-between gap-2 shrink-0">
        <button
          type="button"
          onClick={() => openReader()}
          data-testid="bible-daily-title"
          className="flex items-center gap-2 min-w-0 text-left min-h-11 pointer-coarse:min-h-11 -ml-1 px-1"
        >
          <BookOpen size={16} className="text-amber-300 shrink-0" />
          <h3 className="text-sm font-bold text-white truncate">Verse of the Day</h3>
        </button>
        {settingsButton}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
        {isLoading ? (
          <div className="space-y-2.5 py-1">
            <div className="h-3 w-24 skeleton rounded" />
            <div className="h-3 w-full skeleton rounded" />
            <div className="h-3 w-4/5 skeleton rounded" />
          </div>
        ) : failure || verseError ? (
          <div className="h-full flex flex-col items-center justify-center text-center gap-2 py-3" data-testid="bible-daily-error">
            <p className="text-xs text-amber-300/90 leading-snug">{failure ?? verseError}</p>
            <button
              type="button"
              onClick={() => {
                void trigger('light');
                void refetch();
              }}
              className="glass-button px-3 py-1.5 text-xs inline-flex items-center gap-1.5 min-h-11 pointer-coarse:min-h-11"
            >
              <RefreshCw size={12} className={isFetching ? 'animate-spin' : undefined} />
              Try again
            </button>
          </div>
        ) : !verse ? (
          <div className="h-full flex items-center justify-center text-center py-3">
            <p className="text-xs text-slate-500">No verse available for today.</p>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => openReader(verse.reference)}
            data-testid="bible-daily-verse"
            className="w-full text-left min-h-11 pointer-coarse:min-h-11 rounded-xl px-1 py-1 -mx-1 hover:bg-white/[0.03] transition-colors"
          >
            <p className="text-[11px] font-semibold text-amber-300/90 truncate">{verse.reference}</p>
            <p className="mt-1.5 text-[13px] md:text-sm leading-relaxed text-slate-100 font-serif">
              {verse.text}
            </p>
          </button>
        )}
      </div>

      {roomy && !isLoading && (
        <div className="shrink-0 mt-3 pt-3 border-t border-white/5 space-y-2">
          {devotional && (
            <button
              type="button"
              onClick={() => {
                const entry = data?.devotional?.entry;
                if (entry) void openDevotional(entry);
              }}
              data-testid="bible-daily-devotional"
              className="w-full flex items-start gap-2 text-left min-h-11 pointer-coarse:min-h-11 rounded-lg px-1 -mx-1 hover:bg-white/[0.03] transition-colors"
            >
              <Sparkles size={13} className="text-purple-300 shrink-0 mt-0.5" />
              <span className="min-w-0 flex-1">
                <span className="block text-[10px] uppercase tracking-wider text-slate-500">Devotional</span>
                <span className="block text-xs text-slate-300 truncate">{devotional}</span>
              </span>
              <ExternalLink size={12} className="text-slate-600 shrink-0 mt-0.5" />
            </button>
          )}
          <div className="flex items-center justify-between gap-2">
            <span
              className="inline-flex items-center gap-1.5 text-[11px] text-slate-400"
              data-testid="bible-daily-streak"
            >
              <Flame size={12} className={data?.streaks?.read_streak_current ? 'text-orange-400' : 'text-slate-600'} />
              {data?.streaks
                ? `${data.streaks.read_streak_current} day read streak`
                : 'Reading streak unavailable'}
            </span>
            <button
              type="button"
              onClick={() => openReader()}
              className="shrink-0 glass-button px-3 py-1.5 text-xs font-semibold min-h-11 pointer-coarse:min-h-11"
            >
              Read
            </button>
          </div>
        </div>
      )}
    </div>
  );
}