import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, BookOpen, ExternalLink, Flame, Sparkles } from 'lucide-react';
import { api } from '../../services/api';
import { useHaptics } from '../../hooks/useHaptics';

interface BibleTodayProps {
  onOpenRef: (ref: string) => void;
}

/**
 * Verse of the Day plus today's devotional, the way the Bible App puts them at
 * the top of Home.
 *
 * A devotional source that is not configured is reported here with the setting
 * to fix rather than leaving an empty card -- an empty devotional looks like a
 * bug and reads as a broken product.
 */
export default function BibleToday({ onOpenRef }: BibleTodayProps) {
  const { trigger } = useHaptics();
  const { data, isLoading, error } = useQuery({
    queryKey: ['bible-daily'],
    queryFn: () => api.getBibleDaily(),
    staleTime: 30 * 60 * 1000,
    retry: 1,
  });

  const failure = error instanceof Error ? error.message : null;
  const verse = data && 'error' in data.verse_of_day ? null : data?.verse_of_day;
  const verseError = data && 'error' in data.verse_of_day ? data.verse_of_day.error : null;
  const entry = data?.devotional?.entry ?? null;
  const skipped = data?.devotional?.skipped ?? [];

  return (
    <div className="space-y-4" data-testid="bible-today">
      <section className="glass-panel rounded-2xl p-4 sm:p-5 border border-amber-400/15">
        <div className="flex items-center gap-2 mb-3">
          <BookOpen size={16} className="text-amber-300" />
          <h2 className="text-sm font-bold text-white">Verse of the Day</h2>
          {data?.streaks && (
            <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-slate-400">
              <Flame
                size={13}
                className={data.streaks.read_streak_current ? 'text-orange-400' : 'text-slate-600'}
              />
              {data.streaks.read_streak_current} day streak
            </span>
          )}
        </div>

        {isLoading ? (
          <div className="space-y-2">
            <div className="skeleton h-3 w-28 rounded" />
            <div className="skeleton h-4 w-full rounded" />
            <div className="skeleton h-4 w-3/4 rounded" />
          </div>
        ) : failure || verseError ? (
          <p className="text-sm text-amber-300/90 leading-relaxed">{failure ?? verseError}</p>
        ) : verse ? (
          <button
            type="button"
            onClick={() => {
              void trigger('light');
              onOpenRef(verse.reference);
            }}
            className="w-full text-left min-h-11 pointer-coarse:min-h-11 rounded-xl -mx-1 px-1 py-1 hover:bg-white/[0.03] transition-colors"
          >
            <p className="text-xs font-semibold text-amber-300">{verse.reference}</p>
            <p className="mt-2 font-serif text-lg sm:text-xl leading-relaxed text-slate-100">{verse.text}</p>
          </button>
        ) : (
          <p className="text-sm text-slate-400">No verse available for today.</p>
        )}
      </section>

      <section className="glass-panel rounded-2xl p-4 sm:p-5 border border-white/10">
        <div className="flex items-center gap-2 mb-3">
          <Sparkles size={16} className="text-purple-300" />
          <h2 className="text-sm font-bold text-white">Today's devotional</h2>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            <div className="skeleton h-3 w-40 rounded" />
            <div className="skeleton h-3 w-full rounded" />
          </div>
        ) : entry ? (
          <div className="space-y-2">
            {entry.reference && (
              <button
                type="button"
                onClick={() => onOpenRef(entry.reference)}
                className="text-xs font-semibold text-amber-300/90 min-h-11 pointer-coarse:min-h-11"
              >
                {entry.reference}
              </button>
            )}
            <p className="font-serif text-base leading-relaxed text-slate-200">
              {entry.kind === 'text' ? entry.text : entry.title}
            </p>
            {entry.kind === 'link' && entry.url && (
              <button
                type="button"
                onClick={() => {
                  void trigger('light');
                  window.open(entry.url, '_blank', 'noopener,noreferrer');
                }}
                data-testid="bible-devotional-open"
                className="glass-button px-3 py-2.5 text-xs min-h-11 inline-flex items-center gap-1.5"
              >
                Read at {data?.devotional.source === 'blb' ? 'Blue Letter Bible' : entry.source}
                <ExternalLink size={13} />
              </button>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-slate-400">
              {data?.devotional.reason ?? 'No devotional is available for today.'}
            </p>
            {skipped.length > 0 && (
              <ul className="space-y-1" data-testid="bible-devotional-skipped">
                {skipped.map((s) => (
                  <li key={s.source} className="text-xs text-slate-500 flex items-start gap-1.5">
                    <AlertTriangle size={12} className="mt-0.5 shrink-0 text-amber-400/70" />
                    {s.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
    </div>
  );
}