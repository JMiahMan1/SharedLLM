import type { ReactNode } from 'react';
import { Moon, Smartphone, Users, Utensils } from 'lucide-react';
import { formatMinutes } from '../../lib/healthHub';
import { sharedCycleLine } from '../../lib/healthFamily';
import { useFamilyDirectory } from '../chat/familyDirectory';
import type { HealthFamilyPerson } from '../../types/api';

/**
 * What family members chose to share with the viewer, one line per shared
 * scope so sleep, food and cycle never crowd each other into one cell.
 *
 * Only the scopes the server projected are drawn; the cycle line is phase and
 * next period only, as the server sends it.
 */
export default function SharedWithYouCard({
  people,
  error,
  onRetry,
  renderActions,
}: {
  people: HealthFamilyPerson[];
  /** Set when the family read failed, so the card can say so instead of vanishing. */
  error?: string | null;
  onRetry?: () => void;
  /** Per-person controls under the shared lines (cheers). */
  renderActions?: (person: HealthFamilyPerson) => ReactNode;
}) {
  const lookup = useFamilyDirectory();
  // A failed read used to render nothing at all, which reads the same as a
  // family that has chosen to share nothing.
  if (error) {
    return (
      <section
        className="glass-panel rounded-2xl border border-amber-500/30 p-4"
        aria-labelledby="health-family-heading"
        data-testid="health-family-error"
      >
        <h3
          id="health-family-heading"
          className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
        >
          <Users size={13} className="text-purple-300" />
          Shared with you
        </h3>
        <p className="mt-2 text-xs text-amber-200">{error}</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="glass-button mt-3 inline-flex min-h-10 items-center px-3 text-xs font-semibold pointer-coarse:min-h-11"
          >
            Try again
          </button>
        )}
      </section>
    );
  }
  if (people.length === 0) return null;
  return (
    <section
      className="glass-panel rounded-2xl border border-white/5 p-4"
      aria-labelledby="health-family-heading"
      data-testid="health-family-today"
    >
      <h3
        id="health-family-heading"
        className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
      >
        <Users size={13} className="text-purple-300" />
        Shared with you
      </h3>
      <ul className="mt-2 divide-y divide-white/5">
        {people.map((person) => {
          const name = lookup(person.user_id)?.display_name || person.user_id;
          const cycle = person.cycle;
          const digital = person.digital;
          const cycleLine = sharedCycleLine(cycle);
          const nothing = !person.sleep && !person.nutrition && !cycle && !digital;
          return (
            <li key={person.user_id} className="space-y-1.5 py-2.5 text-xs" data-testid={`shared-${person.user_id}`}>
              <p className="font-semibold text-slate-200">{name}</p>
              {person.sleep && (
                <p className="flex min-w-0 items-center gap-1.5 text-[11px] text-slate-400">
                  <Moon size={12} className="shrink-0 text-sky-300" aria-hidden />
                  <span className="min-w-0 break-words">
                    {person.sleep.logged
                      ? `Slept ${formatMinutes(person.sleep.duration_minutes)}${
                          person.sleep.score !== null ? ` · ${person.sleep.score}/100` : ''
                        }`
                      : 'No sleep logged for last night'}
                  </span>
                </p>
              )}
              {person.nutrition && (
                <p className="flex min-w-0 items-center gap-1.5 text-[11px] text-slate-400">
                  <Utensils size={12} className="shrink-0 text-emerald-300" aria-hidden />
                  <span className="min-w-0 break-words">
                    {!person.nutrition.logged
                      ? 'No food logged today'
                      : person.nutrition.calories !== null
                        ? `${Math.round(person.nutrition.calories).toLocaleString()} kcal logged`
                        : 'Food logged today'}
                  </span>
                </p>
              )}
              {digital && (
                <p className="flex min-w-0 items-center gap-1.5 text-[11px] text-slate-400" data-testid={`shared-digital-${person.user_id}`}>
                  <Smartphone size={12} className="shrink-0 text-sky-300" aria-hidden />
                  <span className="min-w-0 break-words">
                    {!digital.available
                      ? 'Phone use not available right now'
                      : digital.screen_time_min === null
                        ? 'No phone use reported today'
                        : `${formatMinutes(digital.screen_time_min)} on the phone today`}
                  </span>
                </p>
              )}
              {cycle && (
                <p className="text-[11px] text-slate-400" data-testid={`shared-cycle-${person.user_id}`}>
                  {cycleLine ?? (cycle.enabled ? 'Cycle shared · no estimate yet' : 'Cycle tracking is off')}
                </p>
              )}
              {cycle?.enabled && cycle.support && cycle.support.tips.length > 0 && (
                <div className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                  <p className="text-[11px] font-semibold text-slate-300">{cycle.support.title}</p>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-slate-400">
                    {cycle.support.tips.map((tip) => (
                      <li key={tip}>{tip}</li>
                    ))}
                  </ul>
                </div>
              )}
              {nothing && <p className="text-[11px] text-slate-500">Nothing shared yet</p>}
              {renderActions?.(person)}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
