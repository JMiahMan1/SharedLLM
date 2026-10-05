import { useQuery } from '@tanstack/react-query';
import { Award, BookMarked, Flame, Trash2, Users } from 'lucide-react';
import { api } from '../../services/api';
import type { BibleMark } from '../../types/api';

interface BibleProgressProps {
  marks: BibleMark[];
  onDeleteMark: (markId: number) => void;
  onOpenRef: (ref: string) => void;
}

/**
 * Reading streaks, badges, saved verses, and who else in the house is reading.
 *
 * The family list is opt-in on both sides: a reader who has not shared their
 * Bible activity is simply not in the feed, and asking for one who has not
 * shared is a 404 that reads as "not shared" rather than as a failure.
 */
export default function BibleProgress({ marks, onDeleteMark, onOpenRef }: BibleProgressProps) {
  const { data: stats } = useQuery({ queryKey: ['bible-stats'], queryFn: () => api.getBibleStats(30) });
  const { data: achievements } = useQuery({
    queryKey: ['bible-achievements'],
    queryFn: () => api.getBibleAchievements(),
  });
  const { data: feed, isLoading: feedLoading, error: feedError } = useQuery({
    queryKey: ['bible-activity-feed'],
    queryFn: () => api.getBibleActivityFeed(),
    retry: 0,
  });

  const feedFailure = feedError instanceof Error ? feedError.message : null;

  return (
    <div className="space-y-4" data-testid="bible-progress">
      <section className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {[
          { label: 'Read streak', value: stats?.read_streak_current ?? 0, icon: Flame },
          { label: 'Best streak', value: stats?.read_streak_longest ?? 0, icon: Flame },
          { label: 'Days read', value: stats?.days_read ?? 0, icon: BookMarked },
          { label: 'Chapters', value: stats?.metrics?.chapters_total ?? 0, icon: BookMarked },
        ].map(({ label, value, icon: Icon }) => (
          <div key={label} className="glass-panel rounded-2xl p-3">
            <div className="flex items-center gap-1.5 text-slate-500">
              <Icon size={12} />
              <span className="text-[10px] uppercase tracking-wider truncate">{label}</span>
            </div>
            <p className="mt-1.5 text-2xl font-bold text-white tabular-nums">{value}</p>
          </div>
        ))}
      </section>

      <section className="glass-panel rounded-2xl p-4 sm:p-5">
        <div className="flex items-center gap-2 mb-3">
          <Award size={16} className="text-amber-300" />
          <h2 className="text-sm font-bold text-white">Badges</h2>
          {achievements && (
            <span className="ml-auto text-xs text-slate-400">
              {achievements.points} point{achievements.points === 1 ? '' : 's'}
            </span>
          )}
        </div>

        {achievements?.newly_earned.length ? (
          <div className="mb-3 rounded-xl bg-amber-400/10 border border-amber-300/30 p-3" data-testid="bible-new-badge">
            <p className="text-xs font-semibold text-amber-200">
              Unlocked: {achievements.newly_earned.map((a) => a.name).join(', ')}
            </p>
            <p className="text-[11px] text-amber-200/70 mt-1">{achievements.stars.status}</p>
          </div>
        ) : null}

        {achievements && achievements.earned.length > 0 && (
          <ul className="flex flex-wrap gap-2 mb-3">
            {achievements.earned.map((badge) => (
              <li
                key={badge.id}
                className="px-2.5 py-1.5 rounded-lg bg-amber-400/10 border border-amber-300/25 text-xs text-amber-200"
                title={badge.description}
              >
                {badge.name}
              </li>
            ))}
          </ul>
        )}

        {achievements?.next_up.length ? (
          <div className="space-y-2">
            {achievements.next_up.slice(0, 3).map((next) => (
              <div key={next.id} className="space-y-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-xs text-slate-300 truncate">{next.name}</span>
                  <span className="text-[11px] text-slate-500 tabular-nums shrink-0">
                    {next.current}/{next.target}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-purple-400/70"
                    style={{ width: `${Math.min(100, Math.max(0, next.percent))}%` }}
                  />
                </div>
              </div>
            ))}
            {achievements.pending_rules.length > 0 && (
              <p className="text-[11px] text-slate-500 pt-1">
                Plans, memorisation and quiz badges arrive with those features.
              </p>
            )}
          </div>
        ) : (
          <p className="text-xs text-slate-500">Read a chapter to earn the first badge.</p>
        )}
      </section>

      <section className="glass-panel rounded-2xl p-4 sm:p-5">
        <div className="flex items-center gap-2 mb-3">
          <BookMarked size={16} className="text-sky-300" />
          <h2 className="text-sm font-bold text-white">Saved verses</h2>
          <span className="ml-auto text-xs text-slate-500">{marks.length}</span>
        </div>
        {marks.length === 0 ? (
          <p className="text-xs text-slate-500">
            Tap any verse while reading to highlight it or bookmark it. Saved verses sync to every device you sign in on.
          </p>
        ) : (
          <ul className="space-y-2">
            {marks.map((mark) => (
              <li
                key={mark.id}
                className="flex items-start gap-3 rounded-xl bg-black/20 border border-white/5 px-3 py-2.5"
              >
                <button
                  type="button"
                  onClick={() => onOpenRef(`${mark.book_name} ${mark.chapter}:${mark.verse_start}`)}
                  className="flex-1 min-w-0 text-left min-h-11 pointer-coarse:min-h-11"
                >
                  <span className="block text-xs font-semibold text-amber-300 truncate">
                    {mark.book_name} {mark.chapter}:{mark.verse_start}
                    {mark.verse_end && mark.verse_end !== mark.verse_start ? `-${mark.verse_end}` : ''}
                  </span>
                  <span className="block text-xs text-slate-400 truncate">
                    {mark.note_preview || (mark.kind === 'bookmark' ? 'Bookmark' : 'Highlight')}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => onDeleteMark(mark.id)}
                  aria-label={`Remove ${mark.book_name} ${mark.chapter}:${mark.verse_start}`}
                  className="min-h-11 min-w-11 shrink-0 flex items-center justify-center rounded-lg text-slate-500 hover:text-red-300"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="glass-panel rounded-2xl p-4 sm:p-5">
        <div className="flex items-center gap-2 mb-3">
          <Users size={16} className="text-emerald-300" />
          <h2 className="text-sm font-bold text-white">Reading together</h2>
        </div>
        {feedLoading ? (
          <div className="skeleton h-10 rounded-xl" />
        ) : feedFailure ? (
          <p className="text-xs text-amber-300/90">{feedFailure}</p>
        ) : !feed?.entries.length ? (
          <p className="text-xs text-slate-500">
            {feed?.note ?? 'Nobody has shared their reading yet. Share it in Settings → Share my activity.'}
          </p>
        ) : (
          <ul className="space-y-2">
            {feed.entries.map((entry) => (
              <li key={entry.username} className="flex items-center gap-3 rounded-xl bg-black/20 border border-white/5 px-3 py-2.5">
                <span className="flex-1 min-w-0">
                  <span className="block text-sm text-slate-200 truncate">{entry.username}</span>
                  <span className="block text-[11px] text-slate-500">
                    {entry.read_streak_current} day streak · {entry.days_read} days read
                    {entry.achievements.length ? ` · ${entry.achievements.length} badges` : ''}
                  </span>
                </span>
                <span className="shrink-0 text-xs text-amber-300 tabular-nums">{entry.points} pts</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}