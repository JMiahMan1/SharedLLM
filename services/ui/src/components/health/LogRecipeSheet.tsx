import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Utensils, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import { localDay } from '../../lib/healthHub';
import { errorText } from '../../lib/errorText';
import {
  PORTIONS,
  parseRecipeNutrition,
  portionLabel,
  portionTotals,
  recipeFoodEntry,
} from '../../lib/recipeNutrition';
import type { FoodEntry } from '../../types/api';

const MEALS: FoodEntry['meal'][] = ['breakfast', 'lunch', 'dinner', 'snack'];
const EMPTY_TYPED = { calories: '', protein: '', carbs: '', fat: '' };

/**
 * Log a recipe to today's Health day: pick a portion, pick the meal (dinner
 * by default), log. Values come from the note's per-serving nutrition line;
 * when the note has none, the sheet says so and asks for them by hand rather
 * than estimating.
 */
export default function LogRecipeSheet({
  title,
  path,
  body,
  onClose,
}: {
  title: string;
  /** The note's path, stored on the entry so Health links back to the recipe. */
  path: string;
  /** The recipe note body (header optional). */
  body: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const day = useMemo(() => localDay(), []);
  const parsed = useMemo(() => parseRecipeNutrition(body), [body]);
  const [servings, setServings] = useState<number>(1);
  const [meal, setMeal] = useState<FoodEntry['meal']>('dinner');
  const [typed, setTyped] = useState(EMPTY_TYPED);

  const typedCalories = Number(typed.calories);
  const typedValues =
    typed.calories.trim() && Number.isFinite(typedCalories) && typedCalories >= 0
      ? {
          calories: typedCalories,
          // Blank macros log as 0, the same as Nutrition's own "By hand" entry.
          protein_g: Number(typed.protein) || 0,
          carbs_g: Number(typed.carbs) || 0,
          fat_g: Number(typed.fat) || 0,
          fiber_g: null,
          sugar_g: null,
          sodium_mg: null,
        }
      : null;
  const perServing = parsed.status === 'ok' ? parsed.perServing : typedValues;
  const totals = perServing ? portionTotals(perServing, servings) : null;

  const log = useMutation({
    mutationFn: () => {
      if (!perServing) throw new Error('Enter the calories per serving first');
      return api.addFood(
        recipeFoodEntry({ title, path, perServing, servings, day, meal, typed: parsed.status !== 'ok' }),
      );
    },
    onSuccess: () => {
      toast.success(`${title} logged to Health`);
      void queryClient.invalidateQueries({ queryKey: ['health-nutrition'] });
      void queryClient.invalidateQueries({ queryKey: ['health-summary'] });
      void queryClient.invalidateQueries({ queryKey: ['health-food'] });
      void queryClient.invalidateQueries({ queryKey: ['health-challenges'] });
      onClose();
    },
    onError: (error) => toast.error(errorText(error, 'Could not log the recipe')),
  });

  const heading = `Log ${title} to Health`;

  return (
    <div className="fixed inset-0 z-[60] flex items-end sm:items-center sm:justify-center" role="dialog" aria-modal="true" aria-label={heading}>
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative flex max-h-[85vh] w-full flex-col overflow-y-auto border border-b-0 border-white/10 bg-slate-950/97 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:max-w-md sm:rounded-2xl sm:border-b"
        data-testid="log-recipe-sheet"
      >
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="flex items-center gap-2 text-sm font-semibold text-white">
            <Utensils size={16} className="text-emerald-300" /> {heading}
          </p>
          <button type="button" onClick={onClose} aria-label="Close" className="flex min-h-11 min-w-11 items-center justify-center rounded-xl text-slate-400 hover:text-white">
            <X size={16} />
          </button>
        </div>

        {parsed.status === 'ok' ? (
          <p className="mb-3 rounded-xl border border-white/10 bg-white/5 p-3 text-xs text-slate-300" data-testid="log-recipe-per-serving">
            Per serving{parsed.perServing.source ? ` (${parsed.perServing.source})` : ''}:{' '}
            {Math.round(parsed.perServing.calories)} kcal · protein {parsed.perServing.protein_g} g · carbs{' '}
            {parsed.perServing.carbs_g} g · fat {parsed.perServing.fat_g} g
          </p>
        ) : (
          <div className="mb-3 space-y-2" data-testid="log-recipe-manual">
            <p className="text-xs text-amber-200">
              {parsed.status === 'incomplete'
                ? `This recipe's nutrition is missing ${parsed.missing.join(', ')}, so nothing is filled in for you.`
                : 'This recipe has no per-serving nutrition, so nothing is filled in for you.'}{' '}
              Enter it by hand for one serving:
            </p>
            <div className="grid grid-cols-2 gap-2">
              {([
                ['calories', 'Calories (kcal)'],
                ['protein', 'Protein (g)'],
                ['carbs', 'Carbs (g)'],
                ['fat', 'Fat (g)'],
              ] as Array<[keyof typeof EMPTY_TYPED, string]>).map(([key, label]) => (
                <label key={key} className="text-[11px] text-slate-400">
                  {label}
                  <input
                    type="number"
                    min={0}
                    inputMode="decimal"
                    value={typed[key]}
                    onChange={(event) => setTyped({ ...typed, [key]: event.target.value })}
                    aria-label={label}
                    className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm"
                  />
                </label>
              ))}
            </div>
          </div>
        )}

        <p className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">Portion</p>
        <div className="mb-3 flex gap-2" role="group" aria-label="Servings">
          {PORTIONS.map((portion) => (
            <button
              key={portion}
              type="button"
              aria-pressed={servings === portion}
              aria-label={`${portionLabel(portion)} serving${portion === 1 ? '' : 's'}`}
              onClick={() => setServings(portion)}
              className={`min-h-11 flex-1 rounded-xl border text-sm font-semibold transition-colors ${
                servings === portion
                  ? 'border-emerald-500/40 bg-emerald-500/20 text-emerald-200'
                  : 'border-white/10 bg-white/[0.03] text-slate-300 hover:text-white'
              }`}
            >
              {portionLabel(portion)}
            </button>
          ))}
        </div>

        <label className="mb-3 block text-[11px] uppercase tracking-wider text-slate-500">
          Meal
          <select
            value={meal}
            onChange={(event) => setMeal(event.target.value as FoodEntry['meal'])}
            aria-label="Meal"
            className="glass-input mt-1 min-h-11 w-full px-2 text-base sm:text-sm"
          >
            {MEALS.map((option) => (
              <option key={option} value={option}>
                {option[0].toUpperCase() + option.slice(1)}
              </option>
            ))}
          </select>
        </label>

        {totals && (
          <p className="mb-3 text-xs text-slate-300" data-testid="log-recipe-totals">
            This portion: {totals.calories} kcal · protein {totals.protein_g} g · carbs {totals.carbs_g} g · fat{' '}
            {totals.fat_g} g
          </p>
        )}

        <button
          type="button"
          onClick={() => log.mutate()}
          disabled={!perServing || log.isPending}
          data-testid="log-recipe-submit"
          className="glass-button flex min-h-11 w-full items-center justify-center gap-2 text-sm font-semibold disabled:opacity-50"
        >
          {log.isPending ? <Loader2 size={14} className="animate-spin" /> : <Utensils size={14} />}
          Log {portionLabel(servings)} serving{servings === 1 ? '' : 's'}
        </button>
      </div>
    </div>
  );
}
