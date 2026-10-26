import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Utensils } from 'lucide-react';
import { api } from '../../services/api';
import { readMealPlan } from '../../lib/mealPlanStore';
import { planDayOf } from '../../lib/mealPlan';
import { localDay } from '../../lib/healthHub';
import { parseRecipeNutrition, parseRecipeServings, portionTotals } from '../../lib/recipeNutrition';
import { eatenDinner } from '../../lib/recipeEaten';
import LogRecipeSheet from './LogRecipeSheet';

const STORAGE = 'nextcloud';
const RECIPES_DIR = 'Recipes';

/**
 * Tonight's planned dinner from `Recipes/Meal plan.md`, with its stated
 * nutrition (Phase 10). The note's own per-serving line is shown beside a
 * whole-recipe estimate scaled to the servings the recipe states - nothing is
 * guessed, and a recipe with no nutrition says so instead of showing a number.
 *
 * Below it, what was actually logged for dinner today, so a parent can see the
 * difference: tonight's recipe logged as itself, something else logged instead,
 * or nothing yet. The card hides itself when nothing is planned (and its
 * numbers in kid mode), so an empty day stays quiet.
 */
export default function MealsCard({ kid = false }: { kid?: boolean }) {
  const queryClient = useQueryClient();
  const today = useMemo(() => planDayOf(), []);
  const day = useMemo(() => localDay(), []);
  const [logOpen, setLogOpen] = useState(false);

  const planQuery = useQuery({
    queryKey: ['meal-plan'],
    queryFn: () => readMealPlan(),
    staleTime: 300_000,
    retry: false,
  });

  const dinner = planQuery.data?.plan[today] ?? null;
  const title = dinner?.title ?? '';
  const path = title ? `${RECIPES_DIR}/${title}.md` : '';

  const recipeQuery = useQuery({
    queryKey: ['recipe-note', title],
    queryFn: () => api.readNote(title, STORAGE, path),
    enabled: Boolean(title),
    staleTime: 300_000,
    retry: false,
  });

  // What was actually logged for dinner today, to compare with the plan.
  const foodQuery = useQuery({
    queryKey: ['health-food', day],
    queryFn: () => api.getFoodEntries(day),
    enabled: Boolean(title),
    staleTime: 30_000,
    retry: false,
  });

  const body = recipeQuery.data?.status === 'SUCCESS' ? recipeQuery.data.message ?? '' : '';
  const parsed = useMemo(() => parseRecipeNutrition(body), [body]);
  const servings = useMemo(() => parseRecipeServings(body), [body]);
  const eaten = useMemo(
    () => eatenDinner(path, title, foodQuery.data?.entries ?? []),
    [path, title, foodQuery.data],
  );

  // No plan (or still loading it): stay out of the way.
  if (!title) return null;

  const perServing = parsed.status === 'ok' ? parsed.perServing : null;
  const whole = perServing && servings ? portionTotals(perServing, servings) : null;

  return (
    <section
      className="glass-panel rounded-2xl border border-white/5 p-4"
      aria-labelledby="today-meals-heading"
      data-testid="today-meals-card"
    >
      <div className="flex items-center justify-between gap-2">
        <h3
          id="today-meals-heading"
          className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
        >
          <Utensils size={13} className="text-purple-300" />
          Dinner tonight
        </h3>
        {dinner?.cooked && <span className="text-[10px] uppercase tracking-wider text-emerald-300">cooked</span>}
      </div>
      <p className="mt-2 text-lg font-bold text-white" data-testid="today-meals-title">
        {title}
      </p>
      {!kid && perServing ? (
        <p className="mt-1 text-[11px] text-slate-500" data-testid="today-meals-nutrition">
          {Math.round(perServing.calories)} kcal a serving
          {whole ? ` · whole recipe (${servings} servings): ${whole.calories} kcal` : ''}
        </p>
      ) : null}
      {!kid && !perServing && recipeQuery.isSuccess ? (
        <p className="mt-1 text-[11px] text-slate-500" data-testid="today-meals-no-nutrition">
          This recipe states no nutrition - log it by hand if you want it counted.
        </p>
      ) : null}
      {recipeQuery.isError ? (
        <p className="mt-1 text-[11px] text-amber-300">The recipe could not be read right now.</p>
      ) : null}
      <div className="mt-2 rounded-xl border border-white/10 bg-white/[0.03] p-2" data-testid="today-meals-eaten">
        <p className="text-[10px] uppercase tracking-wider text-slate-500">Logged today</p>
        {foodQuery.isError ? (
          <p className="text-xs text-slate-500" data-testid="today-meals-eaten-error">
            Today&apos;s log could not be read.
          </p>
        ) : !foodQuery.isSuccess ? (
          <p className="text-xs text-slate-500" data-testid="today-meals-eaten-loading">
            Checking today&apos;s log…
          </p>
        ) : eaten.matched ? (
          <p className="text-xs text-emerald-300" data-testid="today-meals-eaten-match">
            {eaten.matched.name}
            {eaten.matched.servings
              ? ` · ${eaten.matched.servings} serving${eaten.matched.servings === 1 ? '' : 's'}`
              : ''}
            {!kid ? ` · ${Math.round(eaten.matched.calories * eaten.matched.servings)} kcal` : ''}
          </p>
        ) : eaten.entries.length > 0 ? (
          <p className="text-xs text-amber-300" data-testid="today-meals-eaten-other">
            {eaten.names.join(', ')} - not tonight&apos;s plan
          </p>
        ) : (
          <p className="text-xs text-slate-500" data-testid="today-meals-eaten-none">
            Nothing logged for dinner yet
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={() => setLogOpen(true)}
        disabled={!body}
        data-testid="today-meals-log"
        className="mt-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 text-xs font-semibold text-slate-300 hover:text-white disabled:opacity-50"
      >
        <Utensils size={13} />
        Log tonight&apos;s dinner
      </button>
      {logOpen && body ? (
        <LogRecipeSheet
          title={title}
          path={path}
          body={body}
          planDay={today}
          onMarkedCooked={() => void queryClient.invalidateQueries({ queryKey: ['meal-plan'] })}
          onClose={() => setLogOpen(false)}
        />
      ) : null}
    </section>
  );
}
