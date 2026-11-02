import { useMemo, useState } from 'react';
import { ChefHat, Clock, Flame, Loader2, RotateCcw, Search, Utensils } from 'lucide-react';
import type { CookbookRecipe } from '../../types/api';
import { MEAL_LABELS, PROTEIN_LABELS, formatCookTime } from '../../lib/recipeDoc';
import { sortRecipes, type RecipeSort } from '../../lib/recipeSort';
import RecipePhoto from './RecipePhoto';
import StarRating from './StarRating';

const SORTS: [RecipeSort, string][] = [
  ['top', 'Top rated'],
  ['made', 'Most made'],
  ['staples', 'Uses most staples'],
  ['quick', 'Quickest'],
  ['new', 'Newest'],
  ['az', 'A to Z'],
];

const QUICK = [
  ['quick', 'Under 30 min'],
  ['staples', 'Pantry staples'],
  ['favorites', '4★ and up'],
  ['untried', 'Never made'],
  ['mine', 'Added by me'],
] as const;
type Quick = (typeof QUICK)[number][0];

function Chip({ active, onClick, children, testId }: { active: boolean; onClick: () => void; children: React.ReactNode; testId?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      data-testid={testId}
      className={`min-h-9 shrink-0 rounded-full border px-3 text-xs transition-colors pointer-coarse:min-h-11 ${
        active
          ? 'border-purple-400/60 bg-purple-500/25 text-white'
          : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/20 hover:text-white'
      }`}
    >
      {children}
    </button>
  );
}

function RecipeTile({ recipe, onOpen }: { recipe: CookbookRecipe; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid={`recipe-tile-${recipe.path}`}
      className="group flex flex-col overflow-hidden rounded-2xl border border-white/5 bg-white/[0.03] text-left transition hover:-translate-y-0.5 hover:border-purple-400/40 hover:bg-white/[0.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-purple-400"
    >
      <div className="relative aspect-[4/3] w-full overflow-hidden">
        <RecipePhoto
          path={recipe.image ? recipe.path : null}
          meal={recipe.meal}
          className="h-full w-full transition-transform duration-300 group-hover:scale-105"
        />
        {recipe.made > 0 && (
          <span className="absolute left-2 top-2 rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-semibold text-white backdrop-blur">
            Made {recipe.made}×
          </span>
        )}
        {recipe.total_minutes ? (
          <span className="absolute bottom-2 right-2 flex items-center gap-1 rounded-full bg-black/60 px-2 py-0.5 text-[10px] text-white backdrop-blur">
            <Clock size={10} aria-hidden /> {formatCookTime(recipe.total_minutes)}
          </span>
        ) : null}
      </div>
      <div className="flex flex-1 flex-col gap-1 p-3">
        <span className="line-clamp-2 text-sm font-semibold leading-snug text-slate-100">{recipe.title}</span>
        <span className="flex items-center gap-1.5 text-[11px] text-slate-400">
          <StarRating value={recipe.rating} size={11} />
          {recipe.ratings > 0 ? <span>{recipe.rating?.toFixed(1)}</span> : <span className="text-slate-500">Not rated</span>}
        </span>
        <span className="mt-auto flex flex-wrap gap-1 pt-1 text-[10px] text-slate-400">
          <span className="rounded-full bg-white/5 px-1.5 py-0.5">{MEAL_LABELS[recipe.meal] ?? recipe.meal}</span>
          {recipe.proteins.slice(0, 2).map((protein) => (
            <span key={protein} className="rounded-full bg-white/5 px-1.5 py-0.5">
              {PROTEIN_LABELS[protein] ?? protein}
            </span>
          ))}
          {recipe.calories ? (
            <span className="flex items-center gap-0.5 rounded-full bg-white/5 px-1.5 py-0.5">
              <Flame size={9} aria-hidden /> {recipe.calories}
            </span>
          ) : null}
        </span>
      </div>
    </button>
  );
}

/**
 * The family cookbook as a shelf of cards: search, then narrow by the kind of
 * meal, the main protein and a few quick filters, sorted by what the family
 * likes, makes most, or can cook from the pantry.
 */
export default function RecipeBrowser({
  recipes,
  me,
  loading,
  error,
  onRetry,
  onOpen,
}: {
  recipes: CookbookRecipe[];
  me: string;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpen: (recipe: CookbookRecipe) => void;
}) {
  const [query, setQuery] = useState('');
  const [meal, setMeal] = useState<string | null>(null);
  const [protein, setProtein] = useState<string | null>(null);
  const [quick, setQuick] = useState<Set<Quick>>(new Set());
  const [sort, setSort] = useState<RecipeSort>('top');

  const meals = useMemo(() => Object.keys(MEAL_LABELS).filter((key) => recipes.some((r) => r.meal === key)), [recipes]);
  const proteins = useMemo(
    () => Object.keys(PROTEIN_LABELS).filter((key) => recipes.some((r) => r.proteins.includes(key))),
    [recipes],
  );

  const visible = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const filtered = recipes.filter((recipe) => {
      if (meal && recipe.meal !== meal) return false;
      if (protein && !recipe.proteins.includes(protein)) return false;
      if (quick.has('quick') && !(recipe.total_minutes && recipe.total_minutes <= 30)) return false;
      if (quick.has('staples') && recipe.staples.length < 3) return false;
      if (quick.has('favorites') && !((recipe.rating ?? 0) >= 4)) return false;
      if (quick.has('untried') && recipe.made > 0) return false;
      if (quick.has('mine') && recipe.owner !== me) return false;
      if (words.length) {
        const hay = [recipe.title, ...recipe.tags, ...recipe.staples, recipe.meal, ...recipe.proteins, recipe.owner_label]
          .join(' ')
          .toLowerCase();
        if (!words.every((word) => hay.includes(word))) return false;
      }
      return true;
    });
    return sortRecipes(filtered, sort);
  }, [recipes, query, meal, protein, quick, sort, me]);

  const toggleQuick = (key: Quick) =>
    setQuick((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const filtering = Boolean(query.trim() || meal || protein || quick.size);
  const reset = () => {
    setQuery('');
    setMeal(null);
    setProtein(null);
    setQuick(new Set());
  };

  return (
    <div className="space-y-3" data-testid="recipe-browser">
      <div className="flex items-center gap-2">
        <label className="relative min-w-0 flex-1">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-slate-500" aria-hidden />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search the cookbook: chili, cheese, Mom…"
            aria-label="Search the cookbook"
            type="search"
            className="glass-input min-h-11 w-full py-2 pl-9 pr-3 text-base sm:text-sm"
          />
        </label>
        <select
          value={sort}
          onChange={(event) => setSort(event.target.value as RecipeSort)}
          aria-label="Sort recipes"
          className="glass-input min-h-11 w-32 shrink-0 px-2 text-base sm:w-48 sm:px-3 sm:text-sm"
        >
          {SORTS.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {meals.length > 1 && (
        <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1" role="group" aria-label="Kind of meal">
          <Chip active={!meal} onClick={() => setMeal(null)}>
            <ChefHat size={12} className="mr-1 inline" aria-hidden />
            All meals
          </Chip>
          {meals.map((key) => (
            <Chip key={key} active={meal === key} onClick={() => setMeal(meal === key ? null : key)} testId={`meal-chip-${key}`}>
              {MEAL_LABELS[key]}
            </Chip>
          ))}
        </div>
      )}
      <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
      {proteins.length > 1 && (
        <div className="flex shrink-0 gap-1.5" role="group" aria-label="Main protein">
          <Chip active={!protein} onClick={() => setProtein(null)}>
            <Utensils size={12} className="mr-1 inline" aria-hidden />
            Any protein
          </Chip>
          {proteins.map((key) => (
            <Chip key={key} active={protein === key} onClick={() => setProtein(protein === key ? null : key)} testId={`protein-chip-${key}`}>
              {PROTEIN_LABELS[key]}
            </Chip>
          ))}
        </div>
      )}
      {proteins.length > 1 && <span className="my-1.5 w-px shrink-0 bg-white/10" aria-hidden />}
      <div className="flex shrink-0 gap-1.5" role="group" aria-label="Quick filters">
        {QUICK.map(([key, label]) => (
          <Chip key={key} active={quick.has(key)} onClick={() => toggleQuick(key)} testId={`quick-chip-${key}`}>
            {label}
          </Chip>
        ))}
        {filtering && (
          <button type="button" onClick={reset} className="flex min-h-9 shrink-0 items-center gap-1 px-2 text-xs text-slate-400 hover:text-white">
            <RotateCcw size={12} aria-hidden /> Clear
          </button>
        )}
      </div>
      </div>

      {loading && (
        <p className="flex items-center gap-2 py-10 text-sm text-slate-400">
          <Loader2 size={16} className="animate-spin" /> Opening the cookbook…
        </p>
      )}
      {error && (
        <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-xs text-amber-200">
          {error}{' '}
          <button type="button" onClick={onRetry} className="font-semibold underline">
            Try again
          </button>
        </div>
      )}
      {!loading && !error && recipes.length === 0 && (
        <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-10 text-center text-sm text-slate-400">
          The cookbook is empty. Add the first recipe with “Add a recipe”.
        </p>
      )}
      {!loading && recipes.length > 0 && (
        <p className="text-[11px] text-slate-500" aria-live="polite">
          {visible.length === recipes.length ? `${recipes.length} recipes` : `${visible.length} of ${recipes.length} recipes`}
        </p>
      )}
      {!loading && recipes.length > 0 && visible.length === 0 && (
        <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-8 text-center text-sm text-slate-400">
          Nothing matches.{' '}
          <button type="button" onClick={reset} className="font-semibold text-purple-300 underline">
            Clear the filters
          </button>
        </p>
      )}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
        {visible.map((recipe) => (
          <RecipeTile key={recipe.path} recipe={recipe} onOpen={() => onOpen(recipe)} />
        ))}
      </div>
    </div>
  );
}
