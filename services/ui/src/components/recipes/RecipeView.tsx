import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import {
  ArrowLeft, CalendarPlus, Camera, Check, ChefHat, ExternalLink, Loader2, Minus, MoreHorizontal, Pencil, Plus,
  Send, Share2, Trash2, Utensils,
} from 'lucide-react';
import { api } from '../../services/api';
import type { CookbookRecipe, RecipeEstimate } from '../../types/api';
import { MEAL_LABELS, PROTEIN_LABELS, formatCookTime, parseRecipeDoc } from '../../lib/recipeDoc';
import { scaleIngredient } from '../../lib/recipeScale';
import { parseRecipeNutrition, portionTotals } from '../../lib/recipeNutrition';
import { PLAN_DAYS, planDayOf, type PlanDay, type WeeklyPlan } from '../../lib/mealPlan';
import { weekDates } from '../../lib/skylightMeals';
import { recipeSharePreview, recipeShareText } from '../../lib/recipeShare';
import { localDay } from '../../lib/healthHub';
import { attachmentName } from '../../lib/noteAttachments';
import { recipePhotoUrl } from '../../lib/recipePhoto';
import { useMediaToken } from '../../lib/mediaToken';
import { confirmDialog } from '../ui/confirmDialog';
import RecipePhoto from './RecipePhoto';
import StarRating from './StarRating';

export interface RecipeShareRequest {
  reference: string;
  text: string;
  heading: string;
  buildMessage: (note: string) => string;
}

/** The link inside an attachment line: `.attachments.42/card.jpg`. */
function attachmentTarget(line: string): string {
  return /\]\(<?([^)>\s]+)>?\)/.exec(line)?.[1] ?? '';
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0 px-2 text-center">
      <p className="truncate text-base font-semibold text-white sm:text-lg">{value}</p>
      <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
    </div>
  );
}

/**
 * The pictures a recipe keeps beside it (the card or page it was read from),
 * each a thumbnail that opens the full picture.
 */
function OriginalPictures({ path, lines }: { path: string; lines: string[] }) {
  useMediaToken();
  const pictures = lines.map(attachmentTarget).filter(Boolean);
  if (pictures.length === 0) return null;
  return (
    <section className="glass-panel rounded-3xl border border-white/5 p-5" aria-labelledby="original-heading" data-testid="recipe-originals">
      <h3 id="original-heading" className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-slate-300">
        <Camera size={14} aria-hidden /> Original recipe
      </h3>
      <div className="mt-3 flex flex-wrap gap-3">
        {pictures.map((link) => {
          const src = recipePhotoUrl({ path, file: link });
          return (
            <a
              key={link}
              href={src ?? undefined}
              target="_blank"
              rel="noreferrer"
              className="group flex w-40 flex-col overflow-hidden rounded-xl border border-white/10 bg-black/20 hover:border-purple-400/40"
              data-testid="recipe-original-link"
            >
              {src ? <img src={src} alt="The original recipe" loading="lazy" className="aspect-[3/4] w-full object-cover transition group-hover:opacity-90" /> : <span className="aspect-[3/4] w-full" />}
              <span className="flex items-center gap-1 truncate px-2 py-1.5 text-[11px] text-purple-300">
                <ExternalLink size={10} aria-hidden /> <span className="truncate">{attachmentName(link)}</span>
              </span>
            </a>
          );
        })}
      </div>
    </section>
  );
}

function dayLabel(day: PlanDay, date: string): string {
  const d = new Date(`${date}T12:00:00`);
  return `${day} ${d.toLocaleDateString([], { month: 'short', day: 'numeric' })}`;
}

/**
 * One recipe to cook from: its photo, who added it, the family's stars and
 * how often it has been made, then the ingredients scaled to the servings
 * wanted (tick them off while shopping or cooking), the steps, and what it
 * states per serving.
 */
export default function RecipeView({
  recipe,
  plan,
  onPlan,
  onBack,
  onEdit,
  onStatsChanged,
  onDeleted,
  onShare,
  onLog,
  skylightOff,
  kid = false,
}: {
  recipe: CookbookRecipe;
  plan: WeeklyPlan;
  onPlan: (day: PlanDay, title: string) => Promise<boolean>;
  onBack: () => void;
  onEdit: (body: string) => void;
  onStatsChanged: () => void;
  onDeleted: () => void;
  onShare: (share: RecipeShareRequest) => void;
  onLog: (title: string, path: string, body: string) => void;
  skylightOff: boolean;
  /** Kid mode hides calorie numbers, so the USDA estimate is not offered. */
  kid?: boolean;
}) {
  const bodyQuery = useQuery({
    queryKey: ['recipe-body', recipe.path],
    queryFn: () => api.cookbookRead({ path: recipe.path }),
    staleTime: 30_000,
  });
  const ok = bodyQuery.data?.status === 'SUCCESS';
  const body = ok ? bodyQuery.data?.message ?? '' : '';
  const doc = useMemo(() => parseRecipeDoc(body), [body]);
  const baseServings = doc.servings ?? recipe.servings;
  // The servings asked for; until then the recipe's own.
  const [servingsPick, setServingsPick] = useState<number | null>(null);
  const servings = servingsPick ?? baseServings ?? 1;
  const [batch, setBatch] = useState(1);
  const [have, setHave] = useState<Set<number>>(new Set());
  const [done, setDone] = useState<Set<number>>(new Set());
  const [menuOpen, setMenuOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<RecipeEstimate | null>(null);
  const [mine, setMine] = useState<number | null>(recipe.my_rating);
  const [rating, setRating] = useState({ value: recipe.rating, count: recipe.ratings });
  const [made, setMade] = useState({ count: recipe.made, mine: recipe.made_by_me, last: recipe.last_made });

  const factor = baseServings ? servings / baseServings : batch;
  const nutrition = useMemo(() => parseRecipeNutrition(body), [body]);
  const dates = useMemo(() => weekDates(), []);
  const today = localDay();
  const madeToday = made.last === today && made.mine > 0;

  // When the recipe states no nutrition, estimate it from the USDA seed. The
  // result is labelled an estimate and names every ingredient it could not
  // match; it is never written into the recipe.
  const runEstimate = async () => {
    if (doc.ingredients.length === 0) {
      toast.error('This recipe has no ingredient lines to estimate from');
      return;
    }
    setBusy('estimate');
    try {
      setEstimate(await api.estimateRecipeNutrition(doc.ingredients));
    } catch {
      toast.error('The estimate could not be built right now');
    } finally {
      setBusy(null);
    }
  };

  const rate = async (stars: number) => {
    const before = mine;
    setMine(stars || null);
    try {
      const stat = await api.rateRecipe(recipe.path, stars);
      setRating({ value: stat.rating, count: stat.ratings });
      onStatsChanged();
    } catch {
      setMine(before);
      toast.error('Could not save your rating');
    }
  };

  const toggleMade = async () => {
    setBusy('made');
    try {
      const stat = madeToday ? await api.recipeUnmade(recipe.path, today) : await api.recipeMade(recipe.path, today);
      setMade({ count: stat.made, mine: stat.made_by_me, last: stat.last_made });
      toast.success(madeToday ? 'Taken back' : 'Marked as made today');
      onStatsChanged();
    } catch {
      toast.error('Could not record that');
    } finally {
      setBusy(null);
    }
  };

  const addToDay = async (day: PlanDay) => {
    setPlanOpen(false);
    if (await onPlan(day, recipe.title)) toast.success(`${recipe.title} is ${day}'s dinner`);
  };

  const sendToSkylight = async () => {
    setMenuOpen(false);
    if (skylightOff) {
      toast.error('Skylight is not connected. Add the account in Settings first.');
      return;
    }
    setBusy('skylight');
    try {
      const res = await api.sendSkylightRecipe({ title: recipe.title, body });
      if (res.status === 'SUCCESS') toast.success(res.message || `Sent “${recipe.title}” to Skylight`);
      else toast.error(res.message || 'Skylight could not take that recipe');
    } catch {
      toast.error('Skylight could not take that recipe');
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    setMenuOpen(false);
    if (!(await confirmDialog({ title: 'Delete this recipe?', body: `Delete “${recipe.title}” from the family cookbook?`, confirmLabel: 'Delete', danger: true }))) return;
    setBusy('delete');
    try {
      const res = await api.cookbookDelete(recipe.path);
      if (res.status === 'SUCCESS') {
        toast.success('Recipe deleted');
        onDeleted();
      } else {
        toast.error(res.message || 'Could not delete that recipe');
      }
    } catch {
      toast.error('Could not delete that recipe');
    } finally {
      setBusy(null);
    }
  };

  const toggle = (set: Set<number>, update: (next: Set<number>) => void, index: number) => {
    const next = new Set(set);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    update(next);
  };

  const perServingCalories = nutrition.status === 'ok' ? Math.round(nutrition.perServing.calories) : null;
  const sourceUrl = /https?:\/\/\S+/.exec(doc.source)?.[0];
  const sourceSite = doc.source.replace(/https?:\/\/\S+\s*-?\s*(imported from\s*)?/i, '').trim();
  const perServing = nutrition.status === 'ok' ? nutrition.perServing : null;
  const total = perServing && baseServings ? portionTotals(perServing, servings) : null;

  return (
    <article className="space-y-4" data-testid="recipe-view">
      <button type="button" onClick={onBack} className="flex min-h-11 items-center gap-1.5 text-sm text-slate-300 hover:text-white">
        <ArrowLeft size={16} /> All recipes
      </button>

      <div className="glass-panel overflow-hidden rounded-3xl border border-white/5 md:grid md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <RecipePhoto path={recipe.image ? recipe.path : null} meal={recipe.meal} className="aspect-[16/10] w-full md:aspect-auto md:h-full md:min-h-80" />
        <div className="flex flex-col gap-4 p-5 sm:p-6">
          <div>
            <p className="flex flex-wrap items-center gap-x-2 text-[11px] font-medium uppercase tracking-wider text-purple-300/90">
              <span className="flex items-center gap-1"><ChefHat size={11} aria-hidden /> {MEAL_LABELS[recipe.meal] ?? recipe.meal}</span>
              {recipe.proteins.map((protein) => (
                <span key={protein}>· {PROTEIN_LABELS[protein] ?? protein}</span>
              ))}
            </p>
            <h2 className="mt-1 text-2xl font-bold leading-tight tracking-tight text-white sm:text-3xl">{recipe.title}</h2>
            <p className="mt-1 text-xs text-slate-400" data-testid="recipe-owner">
              Added by {recipe.can_change && recipe.owner_label !== 'an admin' ? 'you' : recipe.owner_label}
              {sourceUrl ? (
                <>
                  {' · '}
                  <a href={sourceUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-purple-300 hover:underline">
                    {sourceSite || 'source'} <ExternalLink size={10} />
                  </a>
                </>
              ) : sourceSite ? ` · ${sourceSite}` : ''}
            </p>
          </div>

          <div className="grid grid-cols-4 divide-x divide-white/10 rounded-2xl border border-white/5 bg-black/20 py-3" data-testid="recipe-stats">
            <Stat label="Prep" value={formatCookTime(doc.prepMinutes) || '-'} />
            <Stat label="Total" value={formatCookTime(doc.totalMinutes ?? recipe.total_minutes) || '-'} />
            <Stat label="Serves" value={baseServings ?? '-'} />
            {perServingCalories && !kid ? (
              <Stat label="Cal / serving" value={perServingCalories} />
            ) : (
              <Stat label="Level" value={recipe.level ?? '-'} />
            )}
          </div>

          <div className="grid grid-cols-1 gap-3 rounded-2xl border border-white/5 bg-black/20 p-3 sm:grid-cols-2">
            <div>
              <p className="text-[10px] uppercase tracking-wider text-slate-500">Family rating</p>
              <p className="flex items-center gap-1.5 text-sm text-slate-200" data-testid="recipe-family-rating">
                <StarRating value={rating.value} size={13} />
                {rating.count ? `${rating.value?.toFixed(1)} (${rating.count})` : 'None yet'}
              </p>
              <p className="mt-2 text-[10px] uppercase tracking-wider text-slate-500">Your stars</p>
              <StarRating value={mine} onRate={(stars) => void rate(stars)} size={18} label="Your rating" />
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wider text-slate-500">Times made</p>
              <p className="text-2xl font-bold text-white" data-testid="recipe-made-count">{made.count}</p>
              <p className="text-[10px] text-slate-500">{made.last ? `Last ${new Date(`${made.last}T12:00:00`).toLocaleDateString([], { month: 'short', day: 'numeric' })}` : made.count ? '' : 'Not yet'}</p>
              <button
                type="button"
                onClick={() => void toggleMade()}
                disabled={busy === 'made'}
                aria-pressed={madeToday}
                data-testid="recipe-made"
                className={`glass-button mt-2 min-h-11 px-3 text-xs disabled:opacity-60 ${madeToday ? 'ring-1 ring-emerald-400/60' : ''}`}
              >
                {busy === 'made' ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {madeToday ? 'Made today' : 'We made it'}
              </button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <button type="button" onClick={() => setPlanOpen((open) => !open)} aria-expanded={planOpen} className="glass-button min-h-11 px-3 text-xs">
                <CalendarPlus size={14} /> Add to the week
              </button>
              {planOpen && (
                <div className="absolute left-0 z-20 mt-1 w-48 rounded-xl border border-white/10 bg-slate-900/95 p-1 shadow-xl backdrop-blur" role="menu">
                  {PLAN_DAYS.map((day) => (
                    <button
                      key={day}
                      type="button"
                      role="menuitem"
                      onClick={() => void addToDay(day)}
                      className="flex min-h-11 w-full items-center justify-between rounded-lg px-3 text-left text-xs text-slate-200 hover:bg-white/10"
                    >
                      <span className={day === planDayOf() ? 'font-semibold text-white' : ''}>{dayLabel(day, dates[day])}</span>
                      <span className="max-w-24 truncate text-[10px] text-slate-500">{plan[day].title ?? ''}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button type="button" onClick={() => onEdit(body)} disabled={!ok} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60" data-testid="recipe-edit">
              <Pencil size={14} /> {recipe.can_change ? 'Edit' : 'Suggest a change'}
            </button>
            <button type="button" onClick={() => onLog(recipe.title, recipe.path, body)} disabled={!ok} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60" data-testid="recipe-log-health">
              <Utensils size={14} /> Log to Health
            </button>
            <div className="relative">
              <button type="button" onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen} aria-label="More actions" className="glass-button flex h-11 w-11 items-center justify-center p-0">
                {busy === 'skylight' || busy === 'delete' ? <Loader2 size={14} className="animate-spin" /> : <MoreHorizontal size={16} />}
              </button>
              {menuOpen && (
                <div className="absolute right-0 z-20 mt-1 w-52 rounded-xl border border-white/10 bg-slate-900/95 p-1 shadow-xl backdrop-blur" role="menu">
                  <button
                    type="button"
                    role="menuitem"
                    disabled={!ok}
                    onClick={() => {
                      setMenuOpen(false);
                      onShare({
                        reference: recipe.title,
                        text: recipeSharePreview(body),
                        heading: `Share ${recipe.title} to chat`,
                        buildMessage: (note) => recipeShareText(recipe.title, body, note),
                      });
                    }}
                    className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-xs text-slate-200 hover:bg-white/10"
                  >
                    <Share2 size={14} /> Share to family chat
                  </button>
                  <button type="button" role="menuitem" disabled={!ok} onClick={() => void sendToSkylight()} data-testid="recipe-send-skylight" className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-xs text-slate-200 hover:bg-white/10">
                    <Send size={14} /> Send to Skylight
                  </button>
                  {recipe.can_change && (
                    <button type="button" role="menuitem" onClick={() => void remove()} aria-label={`Delete ${recipe.title}`} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-xs text-rose-300 hover:bg-rose-500/10">
                      <Trash2 size={14} /> Delete recipe
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {bodyQuery.isLoading && (
        <p className="flex items-center gap-2 text-sm text-slate-400"><Loader2 size={14} className="animate-spin" /> Opening the recipe…</p>
      )}
      {bodyQuery.data && !ok && <p className="text-sm text-amber-200">{bodyQuery.data.message || 'Could not open that recipe.'}</p>}

      {ok && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <section className="glass-panel space-y-3 rounded-3xl border border-white/5 p-5" aria-labelledby="ingredients-heading">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 id="ingredients-heading" className="text-sm font-semibold uppercase tracking-wider text-slate-300">Ingredients</h3>
              {baseServings ? (
                <div className="flex items-center gap-1" role="group" aria-label="Servings">
                  <button type="button" aria-label="Fewer servings" onClick={() => setServingsPick(Math.max(1, servings - 1))} className="glass-button flex h-11 w-11 items-center justify-center p-0">
                    <Minus size={14} />
                  </button>
                  <span className="min-w-20 text-center text-sm text-white" data-testid="recipe-servings">
                    {servings} serving{servings === 1 ? '' : 's'}
                  </span>
                  <button type="button" aria-label="More servings" onClick={() => setServingsPick(Math.min(60, servings + 1))} className="glass-button flex h-11 w-11 items-center justify-center p-0">
                    <Plus size={14} />
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-1" role="group" aria-label="Batch size">
                  {[0.5, 1, 2, 3].map((n) => (
                    <button key={n} type="button" aria-pressed={batch === n} onClick={() => setBatch(n)} className={`glass-button min-h-11 px-3 text-xs ${batch === n ? 'ring-1 ring-purple-400/60' : ''}`}>
                      {n === 0.5 ? '½' : n}×
                    </button>
                  ))}
                </div>
              )}
            </div>
            {factor !== 1 && baseServings ? (
              <p className="text-[11px] text-slate-500">Scaled from {baseServings} servings. <button type="button" className="underline" onClick={() => setServingsPick(null)}>Reset</button></p>
            ) : null}
            {doc.ingredients.length === 0 ? (
              <p className="text-xs text-slate-500">No ingredients listed.</p>
            ) : (
              <ul className="space-y-0.5" data-testid="recipe-ingredients">
                {doc.ingredients.map((item, index) => (
                  <li key={`${index}-${item}`}>
                    <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg px-2 py-2 hover:bg-white/5">
                      <input type="checkbox" checked={have.has(index)} onChange={() => toggle(have, setHave, index)} className="mt-0.5 h-4 w-4 shrink-0 accent-emerald-400" />
                      <span className={`text-sm leading-snug ${have.has(index) ? 'text-slate-500 line-through' : 'text-slate-100'}`}>
                        {scaleIngredient(item, factor)}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            {perServing ? (
              <div className="rounded-2xl border border-white/5 bg-black/20 p-3 text-xs text-slate-300" data-testid="recipe-nutrition">
                <p className="text-[10px] uppercase tracking-wider text-slate-500">Per serving{perServing.source ? ` · ${perServing.source}` : ''}</p>
                <p className="mt-1">
                  <span className="text-base font-semibold text-white">{Math.round(perServing.calories)}</span> kcal · P {perServing.protein_g} g · C {perServing.carbs_g} g · F {perServing.fat_g} g
                </p>
                {total ? <p className="mt-1 text-slate-500">{servings} servings: {total.calories} kcal in all</p> : null}
              </div>
            ) : !kid ? (
              <div className="space-y-2" data-testid="recipe-estimate">
                <button type="button" onClick={() => void runEstimate()} disabled={busy === 'estimate'} data-testid="recipe-estimate-button" className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
                  {busy === 'estimate' ? <Loader2 size={14} className="animate-spin" /> : <Utensils size={14} />}
                  Estimate nutrition from the ingredients (USDA)
                </button>
                {estimate ? (
                  <div className="space-y-1 rounded-xl border border-amber-400/30 bg-amber-400/10 p-3 text-[11px] text-amber-100" data-testid="recipe-estimate-result">
                    <p className="text-amber-200">Estimate from USDA food data - not the recipe&apos;s own numbers.</p>
                    {!estimate.seed.present ? (
                      <p>USDA food data is not installed on this server.</p>
                    ) : (
                      <>
                        <ul className="space-y-0.5">
                          {estimate.ingredients.map((item, index) => (
                            <li key={`${item.text}-${index}`}>
                              {item.status === 'matched'
                                ? `${item.text} - ${item.food} (${item.grams} g): ${Math.round(item.nutrients?.calories ?? 0)} kcal`
                                : `${item.text} - unknown (${item.reason})`}
                            </li>
                          ))}
                        </ul>
                        <p data-testid="recipe-estimate-total">
                          {estimate.totals
                            ? `${estimate.complete ? 'Whole recipe' : `Whole recipe, partial (${estimate.unknown} unknown)`}: ${Math.round(estimate.totals.calories ?? 0)} kcal${baseServings ? ` · ${Math.round((estimate.totals.calories ?? 0) / baseServings)} kcal a serving` : ''}`
                            : 'No ingredient could be matched, so there is no estimate.'}
                        </p>
                      </>
                    )}
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className="glass-panel space-y-3 rounded-3xl border border-white/5 p-5" aria-labelledby="steps-heading">
            <h3 id="steps-heading" className="text-sm font-semibold uppercase tracking-wider text-slate-300">Steps</h3>
            {doc.intro ? <p className="whitespace-pre-wrap text-sm text-slate-300">{doc.intro}</p> : null}
            {doc.steps.length === 0 ? (
              <p className="text-xs text-slate-500">No steps written down.</p>
            ) : (
              <ol className="space-y-2" data-testid="recipe-steps">
                {doc.steps.map((step, index) => (
                  <li key={`${index}-${step}`}>
                    <button
                      type="button"
                      onClick={() => toggle(done, setDone, index)}
                      aria-pressed={done.has(index)}
                      className={`flex w-full gap-3 rounded-xl px-2 py-2 text-left transition hover:bg-white/5 ${done.has(index) ? 'opacity-50' : ''}`}
                    >
                      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${done.has(index) ? 'bg-emerald-500/30 text-emerald-200' : 'bg-purple-500/25 text-purple-100'}`}>
                        {done.has(index) ? <Check size={13} /> : index + 1}
                      </span>
                      <span className="text-sm leading-relaxed text-slate-100">{step}</span>
                    </button>
                  </li>
                ))}
              </ol>
            )}
            {doc.notes ? (
              <div className="rounded-2xl border border-amber-400/20 bg-amber-400/5 p-3">
                <p className="text-[10px] uppercase tracking-wider text-amber-300/80">Notes</p>
                <p className="mt-1 whitespace-pre-wrap text-sm text-slate-200">{doc.notes}</p>
              </div>
            ) : null}
            {doc.extra ? <pre className="whitespace-pre-wrap font-sans text-sm text-slate-300">{doc.extra.replace(/^## /gm, '')}</pre> : null}
          </section>
        </div>
      )}
      {ok ? <OriginalPictures path={recipe.path} lines={doc.attachments} /> : null}
    </article>
  );
}
