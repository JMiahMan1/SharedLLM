import { useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { CalendarDays, Check, Download, Loader2, Plus, Search, Send, Share2, ShoppingCart, Shuffle, Utensils, X } from 'lucide-react';
import { api } from '../../services/api';
import type { CookbookRecipe } from '../../types/api';
import { PLAN_DAYS, pickSurprise, planDayOf, planShareText, type PlanDay, type WeeklyPlan } from '../../lib/mealPlan';
import { plannedPushDays, weekDates } from '../../lib/skylightMeals';
import { buildShoppingList, ingredientLines } from '../../lib/shoppingList';
import { readShoppingList, saveShoppingList } from '../../lib/shoppingListStore';
import { downloadBlobUrl } from '../../lib/artifactKinds';
import { noteBody } from '../../lib/notesMeta';
import { recipeTitleKey } from '../../lib/recipeDoc';
import RecipePhoto from './RecipePhoto';
import type { RecipeShareRequest } from './RecipeView';

function readByTitle(recipes: CookbookRecipe[], title: string) {
  const key = recipeTitleKey(title);
  const path = recipes.find((recipe) => recipeTitleKey(recipe.title) === key)?.path;
  return api.cookbookRead(path ? { path } : { title });
}

/**
 * The week's dinners, Monday to Sunday, each a recipe from the cookbook. From
 * here the week becomes a shopping list and goes to Skylight (the dinners on
 * their dates, the list to its grocery list), or to a calendar file.
 */
export default function WeekPlan({
  plan,
  save,
  recipes,
  asUser,
  skylightOff,
  onOpen,
  onShare,
  onLog,
  onListBuilt,
}: {
  plan: WeeklyPlan;
  save: (next: WeeklyPlan) => Promise<boolean>;
  recipes: CookbookRecipe[];
  asUser?: 'admin';
  skylightOff: boolean;
  onOpen: (recipe: CookbookRecipe) => void;
  onShare: (share: RecipeShareRequest) => void;
  onLog: (title: string, path: string, body: string, day: PlanDay) => void;
  onListBuilt: () => void;
}) {
  const [picking, setPicking] = useState<PlanDay | null>(null);
  const [pickQuery, setPickQuery] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const dates = useMemo(() => weekDates(), []);
  const today = planDayOf();
  const byTitle = useMemo(() => {
    const map = new Map(recipes.map((recipe) => [recipeTitleKey(recipe.title), recipe]));
    return { get: (title: string) => map.get(recipeTitleKey(title)) };
  }, [recipes]);
  const planned = PLAN_DAYS.map((day) => plan[day].title).filter(Boolean) as string[];
  const repeated = [...new Set(planned.filter((title, index) => planned.indexOf(title) !== index))];

  const choose = async (day: PlanDay, title: string | null) => {
    setPicking(null);
    setPickQuery('');
    await save({ ...plan, [day]: { title, cooked: false } });
  };

  const toggleCooked = async (day: PlanDay) => {
    const entry = plan[day];
    if (!entry.title) return;
    const cooked = !entry.cooked;
    if (!(await save({ ...plan, [day]: { ...entry, cooked } }))) return;
    // A cooked dinner counts as a making of the recipe on its day.
    const recipe = byTitle.get(entry.title);
    if (recipe) {
      try {
        await (cooked ? api.recipeMade(recipe.path, dates[day]) : api.recipeUnmade(recipe.path, dates[day]));
      } catch {
        // The plan is saved; the count catches up next time.
      }
    }
  };

  const surprise = async () => {
    const pool = recipes.filter((r) => r.meal === 'main' || r.meal === 'soup').map((r) => r.title).filter((t) => !planned.includes(t));
    const choices = pool.length ? pool : recipes.map((r) => r.title).filter((t) => !planned.includes(t));
    if (choices.length === 0) {
      toast.error('Every recipe is already in the week');
      return;
    }
    const next = { ...plan };
    for (const day of PLAN_DAYS) {
      if (next[day].title || choices.length === 0) continue;
      // pickSurprise takes the pick out of the pool, so no day repeats it.
      const pick = pickSurprise(choices);
      if (!pick) break;
      next[day] = { title: pick, cooked: false };
    }
    await save(next);
  };

  const buildList = async () => {
    const titles = PLAN_DAYS.filter((day) => !plan[day].cooked).map((day) => plan[day].title).filter(Boolean) as string[];
    if (titles.length === 0) {
      toast.error(planned.length ? 'Every planned dinner is already cooked' : 'Pick some dinners first');
      return;
    }
    setBusy('list');
    try {
      const ingredients: string[] = [];
      for (const title of titles) {
        const res = await readByTitle(recipes, title);
        if (res.status === 'SUCCESS') ingredients.push(...ingredientLines(noteBody(res.message ?? '')));
      }
      const existing = await readShoppingList(asUser);
      const saved = await saveShoppingList(buildShoppingList(existing.text ?? '', ingredients), existing.exists, asUser);
      if (saved.response.status === 'SUCCESS') {
        toast.success('Shopping list ready');
        onListBuilt();
      } else {
        toast.error(saved.response.message || 'Could not build the shopping list');
      }
    } catch {
      toast.error('Could not build the shopping list');
    } finally {
      setBusy(null);
    }
  };

  const pushWeek = async () => {
    if (skylightOff) {
      toast.error('Skylight is not connected. Add the account in Settings first.');
      return;
    }
    const days = plannedPushDays(plan);
    if (days.length === 0) {
      toast.error('Plan some dinners first');
      return;
    }
    setBusy('skylight');
    try {
      const payload: Array<{ date: string; title: string; body: string }> = [];
      for (const entry of days) {
        const res = await readByTitle(recipes, entry.title);
        payload.push({ date: entry.date, title: entry.title, body: res.status === 'SUCCESS' ? noteBody(res.message ?? '') : '' });
      }
      const out = await api.pushSkylightWeek({ days: payload });
      const ok = out.planned?.length ?? 0;
      const failed = out.failed?.length ?? 0;
      if (ok > 0 && failed === 0) toast.success(`Pushed ${ok} dinner${ok === 1 ? '' : 's'} to Skylight`);
      else if (ok > 0) toast.error(`Pushed ${ok}, but ${failed} could not be planned`);
      else toast.error(out.message || 'Skylight could not take the week plan');
    } catch {
      toast.error('Skylight could not take the week plan');
    } finally {
      setBusy(null);
    }
  };

  const downloadIcs = async () => {
    setBusy('ics');
    try {
      const out = await api.mealPlanIcs(asUser);
      if (!out.ics) {
        toast.error('No dinners are planned yet');
        return;
      }
      downloadBlobUrl(URL.createObjectURL(new Blob([out.ics], { type: 'text/calendar' })), 'meal-plan.ics');
      toast.success(`Added ${out.events} dinner${out.events === 1 ? '' : 's'} to the calendar file`);
    } catch {
      toast.error('Could not build the calendar file');
    } finally {
      setBusy(null);
    }
  };

  const logTonight = async (day: PlanDay, title: string) => {
    setBusy(`log-${day}`);
    try {
      const res = await readByTitle(recipes, title);
      if (res.status === 'SUCCESS') onLog(title, String((res.detail as { path?: string } | undefined)?.path ?? ''), noteBody(res.message ?? ''), day);
      else toast.error(res.message || 'Could not read that recipe');
    } catch {
      toast.error('Could not read that recipe');
    } finally {
      setBusy(null);
    }
  };

  const range = `${new Date(`${dates.Mon}T12:00:00`).toLocaleDateString([], { month: 'short', day: 'numeric' })} - ${new Date(`${dates.Sun}T12:00:00`).toLocaleDateString([], { month: 'short', day: 'numeric' })}`;
  const pickList = recipes.filter((recipe) => recipe.title.toLowerCase().includes(pickQuery.trim().toLowerCase()));

  return (
    <div className="space-y-4" data-testid="meal-plan">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">This week&apos;s dinners</h2>
          <p className="text-xs text-slate-400">{range}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void surprise()} className="glass-button min-h-11 px-3 text-xs"><Shuffle size={14} /> Fill the gaps</button>
          <button type="button" onClick={() => onShare({ reference: 'Meal plan', text: planShareText(plan), heading: 'Share the week plan to chat', buildMessage: (note) => planShareText(plan, note) })} className="glass-button min-h-11 px-3 text-xs">
            <Share2 size={14} /> Share
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-7">
        {PLAN_DAYS.map((day) => {
          const entry = plan[day];
          const recipe = entry.title ? byTitle.get(entry.title) : undefined;
          const isToday = day === today;
          return (
            <div key={day} className={`glass-panel flex flex-col overflow-hidden rounded-2xl border ${isToday ? 'border-purple-400/50' : 'border-white/5'}`} data-testid={`plan-day-${day}`}>
              <div className="flex items-center justify-between px-3 pt-3">
                <span className={`text-xs font-semibold uppercase tracking-wider ${isToday ? 'text-purple-200' : 'text-slate-400'}`}>
                  {day} {new Date(`${dates[day]}T12:00:00`).getDate()}
                  {isToday ? ' · today' : ''}
                </span>
                {entry.title && (
                  <label className="flex min-h-9 cursor-pointer items-center gap-1.5 text-[11px] text-slate-300">
                    <input type="checkbox" checked={entry.cooked} onChange={() => void toggleCooked(day)} aria-label={`${day} cooked`} className="h-4 w-4 accent-emerald-400" />
                    Cooked
                  </label>
                )}
              </div>
              {entry.title ? (
                <div className="flex flex-1 flex-col gap-2 p-3">
                  <button type="button" onClick={() => recipe && onOpen(recipe)} disabled={!recipe} className="overflow-hidden rounded-xl text-left">
                    <RecipePhoto path={recipe?.image ? recipe.path : null} meal={recipe?.meal} className={`aspect-[16/9] w-full ${entry.cooked ? 'opacity-50' : ''}`} />
                    <span className={`mt-1.5 line-clamp-2 block text-sm font-semibold ${entry.cooked ? 'text-slate-500 line-through' : 'text-slate-100'}`}>{entry.title}</span>
                  </button>
                  <div className="mt-auto flex flex-wrap gap-1.5">
                    <button type="button" onClick={() => setPicking(day)} className="glass-button min-h-9 px-2 text-[11px] pointer-coarse:min-h-11">Change</button>
                    <button type="button" onClick={() => void choose(day, null)} aria-label={`Clear ${day}`} className="glass-button min-h-9 px-2 text-[11px] pointer-coarse:min-h-11"><X size={12} /></button>
                    {isToday && (
                      <button type="button" onClick={() => void logTonight(day, entry.title as string)} disabled={busy === `log-${day}`} data-testid="plan-log-today" className="glass-button min-h-9 px-2 text-[11px] disabled:opacity-60 pointer-coarse:min-h-11">
                        {busy === `log-${day}` ? <Loader2 size={12} className="animate-spin" /> : <Utensils size={12} />} Log
                      </button>
                    )}
                  </div>
                </div>
              ) : (
                <button type="button" onClick={() => setPicking(day)} aria-label={`Plan for ${day}`} className="m-3 flex min-h-28 flex-1 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-white/15 text-xs text-slate-400 hover:border-purple-400/50 hover:text-white">
                  <Plus size={18} /> Pick a dinner
                </button>
              )}
            </div>
          );
        })}
      </div>
      {repeated.length > 0 && (
        <p className="text-xs text-amber-300" data-testid="plan-repeat">Heads up: {repeated.join(', ')} planned more than once this week.</p>
      )}

      <div className="glass-panel flex flex-wrap items-center gap-2 rounded-2xl border border-white/5 p-3">
        <span className="mr-auto text-xs text-slate-400">{planned.length} of 7 dinners planned</span>
        <button type="button" onClick={() => void buildList()} disabled={busy === 'list'} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
          {busy === 'list' ? <Loader2 size={14} className="animate-spin" /> : <ShoppingCart size={14} />} Build shopping list
        </button>
        <button type="button" onClick={() => void pushWeek()} disabled={busy === 'skylight'} data-testid="plan-send-skylight" className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
          {busy === 'skylight' ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Push week to Skylight
        </button>
        <button type="button" onClick={() => void downloadIcs()} disabled={busy === 'ics' || planned.length === 0} data-testid="plan-ics" className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
          {busy === 'ics' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Calendar file
        </button>
      </div>

      {picking && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-label={`Pick ${picking}'s dinner`} onClick={() => setPicking(null)}>
          <div className="glass-panel flex max-h-[85vh] w-full max-w-lg flex-col rounded-t-3xl border border-white/10 p-4 sm:rounded-3xl" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-white"><CalendarDays size={15} /> {picking}&apos;s dinner</h3>
              <button type="button" onClick={() => setPicking(null)} aria-label="Close" className="flex h-11 w-11 items-center justify-center text-slate-400 hover:text-white"><X size={16} /></button>
            </div>
            <label className="relative mt-2">
              <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden />
              <input autoFocus value={pickQuery} onChange={(event) => setPickQuery(event.target.value)} placeholder="Find a recipe…" aria-label="Find a recipe" className="glass-input min-h-11 w-full pl-9 pr-3 text-base sm:text-sm" />
            </label>
            <ul className="mt-2 flex-1 space-y-1 overflow-y-auto">
              {pickList.map((recipe) => (
                <li key={recipe.path}>
                  <button type="button" onClick={() => void choose(picking, recipe.title)} className="flex min-h-14 w-full items-center gap-3 rounded-xl p-1.5 text-left hover:bg-white/10">
                    <RecipePhoto path={recipe.image ? recipe.path : null} meal={recipe.meal} className="h-12 w-16 shrink-0 rounded-lg !text-xl" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-slate-100">{recipe.title}</span>
                      <span className="block text-[10px] text-slate-500">
                        {[recipe.total_minutes ? `${recipe.total_minutes} min` : '', recipe.made ? `made ${recipe.made}×` : '', recipe.rating ? `${recipe.rating.toFixed(1)}★` : ''].filter(Boolean).join(' · ')}
                      </span>
                    </span>
                    {recipeTitleKey(plan[picking].title ?? '') === recipeTitleKey(recipe.title) && <Check size={14} className="text-emerald-300" />}
                  </button>
                </li>
              ))}
              {pickList.length === 0 && <li className="py-6 text-center text-xs text-slate-500">No recipe matches.</li>}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
