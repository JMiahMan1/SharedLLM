import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, CalendarDays, Link2, Loader2, Plus, Save, Share2, ShoppingCart, Shuffle, Trash2, Users } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import { saveNote } from '../lib/noteSave';
import { noteBody } from '../lib/notesMeta';
import type { ExecutionResponse } from '../types/api';
import SendAsSelector from '../components/chat/SendAsSelector';
import { useSendAsPref } from '../components/chat/sendAsPref';
import ShareToChatSheet from '../components/bible/ShareToChatSheet';
import { recipeSharePreview, recipeShareText } from '../lib/recipeShare';
import { SHOPPING_PATH, SHOPPING_TITLE, buildShoppingList, ingredientLines, shoppingShareText } from '../lib/shoppingList';
import { buildRecipeNote, type ImportedRecipe } from '../lib/recipeImport';
import {
  buildMealPlan,
  emptyPlan,
  parseMealPlan,
  planLevel,
  pickSurprise,
  planShareText,
  PLAN_DAYS,
  PLAN_PATH,
  PLAN_TITLE,
  type PlanDay,
  type WeeklyPlan,
} from '../lib/mealPlan';

export const RECIPES_DIR = 'Recipes';
const STORAGE = 'nextcloud';

interface RecipeSummary {
  title: string;
  path: string;
  modified?: string;
}

/** One pending share: what the sheet shows and the message it builds. */
interface ShareRequest {
  reference: string;
  text: string;
  heading: string;
  previewIcon: string;
  buildMessage: (note: string) => string;
}

function notesOf(response: ExecutionResponse | undefined): RecipeSummary[] {
  const detail = response?.detail as { notes?: RecipeSummary[] } | undefined;
  return detail?.notes ?? [];
}

/**
 * Family recipes, stored as markdown in Nextcloud under `Recipes/`.
 *
 * The same notes storage the rest of the house uses means one backup, one sync
 * and one mobile app. Because notes resolve to the caller's own account, an
 * admin can switch to the Admin identity with the shared "Send as" control to
 * keep one house cookbook, and every other member keeps their own.
 */
export default function Recipes() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<RecipeSummary | null>(null);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [saving, setSaving] = useState(false);
  const [share, setShare] = useState<ShareRequest | null>(null);
  const [sharePath, setSharePath] = useState<string | null>(null);
  const [sharingList, setSharingList] = useState(false);
  const [teaching, setTeaching] = useState(false);
  const [listing, setListing] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importUrl, setImportUrl] = useState('');
  const [importing, setImporting] = useState(false);
  const [imported, setImported] = useState<ImportedRecipe | null>(null);
  const [savingImport, setSavingImport] = useState(false);
  const [filter, setFilter] = useState('');
  const [planOpen, setPlanOpen] = useState(false);
  const [plan, setPlan] = useState<WeeklyPlan>(emptyPlan());
  const [planExists, setPlanExists] = useState(false);
  const [planBusy, setPlanBusy] = useState(false);
  const [planLevels, setPlanLevels] = useState<Record<string, string>>({});

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000 });
  const isAdmin = Boolean(me?.is_admin);
  const [sendAs, setSendAs] = useSendAsPref(isAdmin, 'notes');
  const asUser = isAdmin && sendAs === 'admin' ? ('admin' as const) : undefined;

  const { data, isLoading } = useQuery({
    queryKey: ['recipes', asUser ?? 'me'],
    queryFn: () => api.listNotes({ storage: STORAGE, directories: [RECIPES_DIR], as_user: asUser }),
  });

  const recipes = notesOf(data);

  const read = useMutation({
    mutationFn: (recipe: RecipeSummary) => api.readNote(recipe.title, STORAGE, recipe.path, asUser),
    onSuccess: (res: ExecutionResponse) => {
      // note_read returns the whole file; the editor shows the recipe without
      // the stored header, or every save would stack another copy of it.
      if (res.status === 'SUCCESS') setBody(noteBody(res.message ?? ''));
      else toast.error(res.message || 'Could not open that recipe');
    },
    onError: (error: Error) => toast.error(error.message || 'Could not open that recipe'),
  });

  const startNew = () => {
    setSelected(null);
    setTitle('');
    setBody('');
  };

  const save = async () => {
    const name = title.trim();
    if (!name) {
      toast.error('Give the recipe a name');
      return;
    }
    setSaving(true);
    try {
      // Editing an open recipe saves over it (or renames it); otherwise it is new.
      const saved = await saveNote({
        title: name,
        content: body,
        category: RECIPES_DIR,
        previous: selected ? { title: selected.title, category: RECIPES_DIR, path: selected.path } : undefined,
        asUser,
      });
      if (saved.response.status === 'SUCCESS') {
        toast.success('Recipe saved');
        if (saved.leftOldCopy) toast.error('Saved under the new name, but the old copy is still there');
        startNew();
        queryClient.invalidateQueries({ queryKey: ['recipes'] });
      } else {
        toast.error(saved.response.message || 'Could not save the recipe');
      }
    } catch {
      toast.error('Could not save the recipe');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (recipe: RecipeSummary) => {
    if (!window.confirm(`Delete the recipe "${recipe.title}"?`)) return;
    try {
      const res = await api.deleteNote(recipe.title, STORAGE, recipe.path, asUser);
      if (res.status === 'SUCCESS') {
        toast.success('Recipe removed');
        if (selected?.path === recipe.path) startNew();
        queryClient.invalidateQueries({ queryKey: ['recipes'] });
      } else {
        toast.error(res.message || 'Could not remove that recipe');
      }
    } catch {
      toast.error('Could not remove that recipe');
    }
  };

  const openShare = async (recipe: RecipeSummary) => {
    setSharePath(recipe.path);
    try {
      const res = await api.readNote(recipe.title, STORAGE, recipe.path, asUser);
      if (res.status === 'SUCCESS' && res.message) {
        const recipeBody = noteBody(res.message);
        setShare({
          reference: recipe.title,
          text: recipeSharePreview(recipeBody),
          heading: `Share ${recipe.title} to chat`,
          previewIcon: '🍽',
          buildMessage: (note) => recipeShareText(recipe.title, recipeBody, note),
        });
      } else {
        toast.error(res.message || 'Could not read that recipe');
      }
    } catch {
      toast.error('Could not read that recipe');
    } finally {
      setSharePath(null);
    }
  };

  const teachJarvis = async () => {
    setTeaching(true);
    try {
      const res = await api.syncNotesRag({ directories: [RECIPES_DIR] });
      if (res.status === 'SUCCESS') toast.success('Jarvis is learning the recipes');
      else toast.error(res.message || 'Could not sync the recipes');
    } catch {
      toast.error('Could not sync the recipes');
    } finally {
      setTeaching(false);
    }
  };

  const sharePlan = () => {
    setShare({
      reference: 'Meal plan',
      text: planShareText(plan),
      heading: 'Share the week plan to chat',
      previewIcon: '🍽',
      buildMessage: (note) => planShareText(plan, note),
    });
  };

  const shareList = async () => {
    setSharingList(true);
    try {
      const res = await api.readNote(SHOPPING_TITLE, STORAGE, SHOPPING_PATH, asUser);
      if (res.status !== 'SUCCESS' || !res.message) {
        toast.error('Build the shopping list first');
        return;
      }
      const listBody = res.message;
      setShare({
        reference: 'Shopping list',
        text: shoppingShareText(listBody),
        heading: 'Share the shopping list to chat',
        previewIcon: '🛒',
        buildMessage: (note) => shoppingShareText(listBody, note),
      });
    } catch {
      toast.error('Could not read the shopping list');
    } finally {
      setSharingList(false);
    }
  };

  const loadLevel = async (recipeTitle: string) => {
    if (!recipeTitle || recipeTitle in planLevels) return;
    const recipe = recipes.find((candidate) => candidate.title === recipeTitle);
    if (!recipe) return;
    // One attempt per title; the level chip is optional, never the point.
    setPlanLevels((current) => ({ ...current, [recipeTitle]: '' }));
    try {
      const res = await api.readNote(recipe.title, STORAGE, recipe.path, asUser);
      if (res.status === 'SUCCESS') {
        const level = planLevel(res.message ?? '');
        if (level) setPlanLevels((current) => ({ ...current, [recipeTitle]: level }));
      }
    } catch {
      // The chip stays absent; the plan still works.
    }
  };

  const openPlan = async () => {
    if (planOpen) {
      setPlanOpen(false);
      return;
    }
    setPlanBusy(true);
    try {
      const res = await api.readNote(PLAN_TITLE, STORAGE, PLAN_PATH, asUser);
      if (res.status === 'SUCCESS') {
        setPlan(parseMealPlan(res.message ?? ''));
        setPlanExists(true);
      } else {
        setPlan(emptyPlan());
        setPlanExists(false);
      }
      setPlanOpen(true);
    } catch {
      toast.error('Could not open the week plan');
    } finally {
      setPlanBusy(false);
    }
  };

  const setPlanDay = (day: PlanDay, recipeTitle: string) => {
    setPlan((current) => ({ ...current, [day]: { ...current[day], title: recipeTitle || null } }));
    void loadLevel(recipeTitle);
  };

  const toggleCooked = (day: PlanDay) => {
    setPlan((current) => ({ ...current, [day]: { ...current[day], cooked: !current[day].cooked } }));
  };

  const planSurprise = () => {
    const pool = recipes.map((recipe) => recipe.title).filter((candidate) => !plannedTitles.includes(candidate));
    if (pool.length === 0) {
      toast.error('Every recipe is already in the week');
      return;
    }
    const next = { ...plan };
    for (const day of PLAN_DAYS) {
      if (next[day].title || pool.length === 0) continue;
      const pick = pickSurprise(pool);
      if (!pick) break;
      next[day] = { ...next[day], title: pick };
      void loadLevel(pick);
    }
    setPlan(next);
  };

  const savePlan = async () => {
    setPlanBusy(true);
    try {
      const saved = await saveNote({
        title: PLAN_TITLE,
        content: buildMealPlan(plan),
        category: RECIPES_DIR,
        previous: planExists ? { title: PLAN_TITLE, category: RECIPES_DIR, path: PLAN_PATH } : undefined,
        asUser,
      });
      if (saved.response.status === 'SUCCESS') {
        toast.success('Week plan saved');
        setPlanExists(true);
        queryClient.invalidateQueries({ queryKey: ['recipes'] });
      } else {
        toast.error(saved.response.message || 'Could not save the week plan');
      }
    } catch {
      toast.error('Could not save the week plan');
    } finally {
      setPlanBusy(false);
    }
  };

  const buildList = async () => {
    const planned = PLAN_DAYS.map((day) => plan[day].title).filter(Boolean) as string[];
    if (planned.length === 0) {
      toast.error('Pick some dinners first');
      return;
    }
    setListing(true);
    try {
      const ingredients: string[] = [];
      for (const recipeTitle of planned) {
        const path = recipes.find((recipe) => recipe.title === recipeTitle)?.path ?? `${RECIPES_DIR}/${recipeTitle}.md`;
        const res = await api.readNote(recipeTitle, STORAGE, path, asUser);
        if (res.status === 'SUCCESS') ingredients.push(...ingredientLines(noteBody(res.message ?? '')));
      }
      const existing = await api.readNote(SHOPPING_TITLE, STORAGE, SHOPPING_PATH, asUser);
      const existed = existing.status === 'SUCCESS';
      const saved = await saveNote({
        title: SHOPPING_TITLE,
        content: buildShoppingList(existed ? existing.message ?? '' : '', ingredients),
        category: RECIPES_DIR,
        previous: existed ? { title: SHOPPING_TITLE, category: RECIPES_DIR, path: SHOPPING_PATH } : undefined,
        asUser,
      });
      if (saved.response.status === 'SUCCESS') {
        toast.success('Shopping list ready');
        queryClient.invalidateQueries({ queryKey: ['recipes'] });
      } else {
        toast.error(saved.response.message || 'Could not build the shopping list');
      }
    } catch {
      toast.error('Could not build the shopping list');
    } finally {
      setListing(false);
    }
  };

  const readLink = async () => {
    const url = importUrl.trim();
    if (!url) {
      toast.error('Paste a recipe link first');
      return;
    }
    setImporting(true);
    try {
      const res = await api.importRecipe(url);
      if (res.status === 'SUCCESS' && res.detail?.recipe) {
        setImported(res.detail.recipe as ImportedRecipe);
      } else {
        setImported(null);
        toast.error(res.message || 'That link could not be read');
      }
    } catch {
      toast.error('That link could not be read');
    } finally {
      setImporting(false);
    }
  };

  const saveImported = async () => {
    if (!imported) return;
    if (recipes.some((recipe) => recipe.title === imported.title)) {
      toast.error('A recipe with that name is already on the shelf');
      return;
    }
    setSavingImport(true);
    try {
      const saved = await saveNote({
        title: imported.title,
        content: buildRecipeNote(imported),
        category: RECIPES_DIR,
        asUser,
      });
      if (saved.response.status === 'SUCCESS') {
        toast.success('Recipe saved');
        queryClient.invalidateQueries({ queryKey: ['recipes'] });
        setImported(null);
        setImportUrl('');
        setImportOpen(false);
      } else {
        toast.error(saved.response.message || 'Could not save the recipe');
      }
    } catch {
      toast.error('Could not save the recipe');
    } finally {
      setSavingImport(false);
    }
  };

  const plannedTitles = PLAN_DAYS.map((day) => plan[day].title).filter(Boolean) as string[];
  const repeated = Array.from(
    new Set(plannedTitles.filter((candidate, index) => plannedTitles.indexOf(candidate) !== index)),
  );

  const filterText = filter.trim().toLowerCase();
  const visibleRecipes = filterText
    ? recipes.filter((recipe) => recipe.title.toLowerCase().includes(filterText))
    : recipes;

  return (
    <div className="space-y-4" data-testid="recipes">
      <div className="glass-panel p-4 rounded-2xl border border-white/5 space-y-2">
        <div className="flex items-center gap-2">
          <BookOpen size={16} className="text-orange-300" />
          <span className="text-sm font-semibold text-slate-200">Family recipes</span>
          {asUser === 'admin' && (
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] text-amber-200">
              <Users size={11} /> shared cookbook
            </span>
          )}
        </div>
        <p className="text-xs text-slate-400">
          Saved in Nextcloud under <code className="text-slate-300">{RECIPES_DIR}/</code>: yours, or the house account
          when an admin switches.
        </p>
        <input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filter recipes…"
          aria-label="Filter recipes"
          className="glass-input min-h-11 w-full px-2 py-1 text-base sm:max-w-xs sm:text-sm"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void openPlan()}
            disabled={planBusy}
            className="glass-button min-h-11 px-3 text-xs disabled:opacity-60"
          >
            {planBusy ? <Loader2 size={14} className="animate-spin" /> : <CalendarDays size={14} />}
            {planOpen ? 'Back to recipes' : 'Week plan'}
          </button>
          <button
            type="button"
            onClick={() => void teachJarvis()}
            disabled={teaching}
            className="glass-button min-h-11 px-3 text-xs disabled:opacity-60"
          >
            {teaching ? <Loader2 size={14} className="animate-spin" /> : <BookOpen size={14} />}
            Teach Jarvis
          </button>
          <button
            type="button"
            onClick={() => setImportOpen((open) => !open)}
            className="glass-button min-h-11 px-3 text-xs"
          >
            <Link2 size={14} /> {importOpen ? 'Close import' : 'Import a link'}
          </button>
          {isAdmin && <SendAsSelector value={sendAs} onChange={(next) => { startNew(); setSendAs(next); }} />}
        </div>
      </div>

      {importOpen && (
        <div className="glass-panel space-y-3 rounded-2xl border border-white/5 p-4" data-testid="recipe-import">
          <span className="text-sm font-semibold text-slate-200">Import from a link</span>
          <p className="text-xs text-slate-400">Paste a recipe page — Jarvis reads its published recipe data.</p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={importUrl}
              onChange={(event) => setImportUrl(event.target.value)}
              placeholder="https://…"
              aria-label="Recipe link"
              className="glass-input min-h-11 min-w-0 flex-1 px-2 py-1 text-base sm:text-sm"
            />
            <button
              type="button"
              onClick={() => void readLink()}
              disabled={importing}
              className="glass-button min-h-11 px-3 text-xs disabled:opacity-60"
            >
              {importing ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
              Read the link
            </button>
          </div>
          {imported && (
            <div className="space-y-2 rounded-xl border border-white/10 bg-white/5 p-3" data-testid="recipe-import-preview">
              <div className="text-sm font-semibold text-slate-100">{imported.title}</div>
              <div className="text-xs text-slate-400">
                {imported.site}
                {imported.total_minutes ? ` · ${imported.total_minutes} min total` : ''}
                {imported.yield ? ` · ${imported.yield}` : ''}
                {` · ${imported.ingredients.length} ingredients · ${imported.instructions.length} steps`}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => void saveImported()}
                  disabled={savingImport}
                  className="glass-button min-h-11 px-3 text-xs disabled:opacity-60"
                >
                  {savingImport ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                  Save to Recipes
                </button>
                <button type="button" onClick={() => setImported(null)} className="glass-button min-h-11 px-3 text-xs">
                  Discard
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {planOpen && (
        <div className="glass-panel space-y-3 rounded-2xl border border-white/5 p-4" data-testid="meal-plan">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-semibold text-slate-200">This week&apos;s dinners</span>
            <div className="flex items-center gap-2">
              <button type="button" onClick={planSurprise} className="glass-button min-h-11 px-3 text-xs">
                <Shuffle size={14} /> Surprise me
              </button>
              <button type="button" onClick={sharePlan} className="glass-button min-h-11 px-3 text-xs">
                <Share2 size={14} /> Share plan
              </button>
            </div>
          </div>
          {recipes.length === 0 && (
            <p className="text-xs text-slate-500">Add a few recipes first, then pick one per day.</p>
          )}
          {PLAN_DAYS.map((day) => (
            <div key={day} className="flex flex-wrap items-center gap-2">
              <label className="flex h-11 w-24 shrink-0 items-center gap-2 text-sm text-slate-200">
                <input
                  type="checkbox"
                  checked={plan[day].cooked}
                  onChange={() => toggleCooked(day)}
                  aria-label={`${day} cooked`}
                  className="h-4 w-4 accent-purple-400"
                />
                {day}
              </label>
              <select
                value={plan[day].title ?? ''}
                onChange={(event) => setPlanDay(day, event.target.value)}
                aria-label={`Plan for ${day}`}
                className="glass-input min-h-11 min-w-0 flex-1 px-2 py-1 text-base sm:text-sm"
              >
                <option value="">Pick a recipe…</option>
                {recipes.map((recipe) => (
                  <option key={recipe.path} value={recipe.title}>
                    {recipe.title}
                  </option>
                ))}
              </select>
              {plan[day].title && planLevels[plan[day].title as string] ? (
                <span className="rounded-full border border-purple-400/40 bg-purple-400/10 px-2 py-0.5 text-[10px] text-purple-200">
                  Level {planLevels[plan[day].title as string]}
                </span>
              ) : null}
            </div>
          ))}
          {repeated.length > 0 && (
            <p className="text-xs text-amber-300" data-testid="plan-repeat">
              Heads up: {repeated.join(', ')} planned more than once this week.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void buildList()}
              disabled={listing}
              className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-60"
            >
              {listing ? <Loader2 size={14} className="animate-spin" /> : <ShoppingCart size={14} />}
              Build shopping list
            </button>
            <button
              type="button"
              onClick={() => void shareList()}
              disabled={sharingList}
              className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-60"
            >
              {sharingList ? <Loader2 size={14} className="animate-spin" /> : <Share2 size={14} />}
              Share list
            </button>
            <button
              type="button"
              onClick={() => void savePlan()}
              disabled={planBusy}
              className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-60"
            >
              {planBusy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              Save plan
            </button>
          </div>
        </div>
      )}

      {!planOpen && (
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <div className="space-y-2">
          {isLoading && <p className="text-xs text-slate-500">Loading recipes…</p>}
          {!isLoading && recipes.length === 0 && (
            <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
              No recipes yet. Add the first one on the right.
            </p>
          )}
          {!isLoading && recipes.length > 0 && visibleRecipes.length === 0 && (
            <p className="rounded-2xl border border-white/5 bg-white/5 px-4 py-6 text-center text-sm text-slate-500">
              No recipes match “{filter.trim()}”.
            </p>
          )}
          {visibleRecipes.map((recipe) => (
            <div
              key={recipe.path}
              className={`flex items-center gap-2 rounded-xl border p-1.5 pl-3 ${
                selected?.path === recipe.path ? 'border-purple-400/40 bg-purple-500/10' : 'border-white/5 bg-white/5'
              }`}
            >
              <button
                type="button"
                onClick={() => {
                  setSelected(recipe);
                  setTitle(recipe.title);
                  read.mutate(recipe);
                }}
                aria-current={selected?.path === recipe.path ? 'true' : undefined}
                className="min-h-11 min-w-0 flex-1 truncate text-left text-sm text-slate-100"
              >
                {recipe.title}
              </button>
              <button
                type="button"
                aria-label={`Share ${recipe.title}`}
                onClick={() => void openShare(recipe)}
                disabled={sharePath === recipe.path}
                className="glass-button flex h-11 w-11 shrink-0 items-center justify-center p-0 disabled:opacity-60"
              >
                {sharePath === recipe.path ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Share2 size={14} />
                )}
              </button>
              <button
                type="button"
                aria-label={`Delete ${recipe.title}`}
                onClick={() => void remove(recipe)}
                className="glass-button flex h-11 w-11 shrink-0 items-center justify-center p-0"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              {selected ? 'Editing' : 'New recipe'}
            </span>
            {selected && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    const recipeBody = body;
                    setShare({
                      reference: selected.title,
                      text: recipeSharePreview(recipeBody),
                      heading: `Share ${selected.title} to chat`,
                      previewIcon: '🍽',
                      buildMessage: (note) => recipeShareText(selected.title, recipeBody, note),
                    });
                  }}
                  className="glass-button min-h-11 px-3 text-xs"
                >
                  <Share2 size={14} /> Share to chat
                </button>
                <button type="button" onClick={startNew} className="glass-button min-h-11 px-3 text-xs">
                  <Plus size={14} /> New recipe
                </button>
              </div>
            )}
          </div>
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Recipe name"
            aria-label="Recipe name"
            className="glass-input min-h-11 w-full px-3 py-2 text-base sm:text-sm"
          />
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder="Ingredients and steps (markdown)"
            aria-label="Recipe body"
            rows={10}
            className="glass-input w-full resize-y px-3 py-2 text-base sm:text-sm"
          />
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="glass-button min-h-11 px-3 py-2 text-sm disabled:opacity-60"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : selected ? <Save size={14} /> : <Plus size={14} />}
            {selected ? 'Save changes' : 'Save recipe'}
          </button>
        </div>
      </div>
      )}

      {share && (
        <ShareToChatSheet
          reference={share.reference}
          text={share.text}
          heading={share.heading}
          previewIcon={share.previewIcon}
          buildMessage={share.buildMessage}
          onClose={() => setShare(null)}
        />
      )}
    </div>
  );
}
