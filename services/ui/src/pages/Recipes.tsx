import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { BookOpen, CalendarDays, ChefHat, ImagePlus, Inbox, Loader2, MoreHorizontal, Plus, ShoppingCart, Scissors, Users, X } from 'lucide-react';
import { api } from '../services/api';
import { errorText } from '../lib/errorText';
import { isKidMode } from '../lib/healthSwitches';
import type { CookbookRecipe, ExecutionResponse, RecipeMatch, RecipeDraftResponse } from '../types/api';
import type { PlanDay } from '../lib/mealPlan';
import SendAsSelector from '../components/chat/SendAsSelector';
import { useSendAsPref } from '../components/chat/sendAsPref';
import ShareToChatSheet from '../components/bible/ShareToChatSheet';
import LogRecipeSheet from '../components/health/LogRecipeSheet';
import RecipeBrowser from '../components/recipes/RecipeBrowser';
import RecipeView, { type RecipeShareRequest } from '../components/recipes/RecipeView';
import RecipeEditor from '../components/recipes/RecipeEditor';
import AddRecipe from '../components/recipes/AddRecipe';
import WeekPlan from '../components/recipes/WeekPlan';
import ShoppingList from '../components/recipes/ShoppingList';
import SuggestionsPanel from '../components/recipes/SuggestionsPanel';
import { usePlan } from '../components/recipes/usePlan';

export const RECIPES_DIR = 'Recipes';

type Tab = 'recipes' | 'week' | 'list';

interface LogRequest {
  title: string;
  path: string;
  body: string;
  planDay?: PlanDay;
}

const TABS: [Tab, string, typeof ChefHat][] = [
  ['recipes', 'Recipes', ChefHat],
  ['week', 'This week', CalendarDays],
  ['list', 'Shopping list', ShoppingCart],
];

/**
 * The family cookbook: a shelf everyone browses like a media library (photos,
 * kinds of meal, proteins, ratings, how often each is made), a recipe card to
 * cook from, the week's dinners, and the shopping list they make, with Skylight
 * as the kitchen display.
 *
 * Recipes live in the household account's `Recipes/` folder. Everyone reads
 * every recipe; only whoever added one changes it (an admin, for recipes added
 * as Admin) and anyone else suggests a change for them to approve. The week
 * plan and shopping list are each person's own, or the Admin account's when an
 * admin switches "Send as" to Admin.
 *
 * Where you are is in the address (`?tab=`, `?recipe=`, `?add=1`, `?edit=`),
 * so the phone's Back button walks back out of a recipe.
 */
export default function Recipes() {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = (['week', 'list'].includes(params.get('tab') ?? '') ? params.get('tab') : 'recipes') as Tab;
  const openPath = params.get('recipe');
  const editing = params.get('edit');
  const adding = params.get('add') === '1';
  const [share, setShare] = useState<RecipeShareRequest | null>(null);
  const [logRequest, setLogRequest] = useState<LogRequest | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api.getMe(), staleTime: 300_000 });
  const isAdmin = Boolean(me?.is_admin);
  const { data: healthProfile } = useQuery({ queryKey: ['health-profile'], queryFn: () => api.getHealthProfile(), staleTime: 300_000, retry: false });
  const kid = isKidMode(healthProfile);
  const [sendAs, setSendAs] = useSendAsPref(isAdmin, 'notes');
  const asUser = isAdmin && sendAs === 'admin' ? ('admin' as const) : undefined;

  const catalog = useQuery({ queryKey: ['recipe-catalog'], queryFn: () => api.recipeCatalog(), staleTime: 60_000 });
  const recipes: CookbookRecipe[] = useMemo(() => catalog.data?.recipes ?? [], [catalog.data]);
  const suggestionsQuery = useQuery({ queryKey: ['recipe-suggestions'], queryFn: () => api.cookbookSuggestions(), staleTime: 30_000, retry: false });
  const suggestions = suggestionsQuery.data ?? [];
  const toReview = suggestions.filter((s) => s.can_review).length;
  const personalQuery = useQuery({ queryKey: ['recipe-personal'], queryFn: () => api.cookbookPersonal(), staleTime: 300_000, retry: false });
  const personal = personalQuery.data ?? [];
  const skylightQuery = useQuery({ queryKey: ['skylight-meals'], queryFn: () => api.getSkylightMeals(), staleTime: 300_000, retry: false });
  const skylightOff =
    skylightQuery.data?.configured === false ||
    (skylightQuery.error as { response?: { status?: number } } | null)?.response?.status === 400;
  const { plan, save: savePlan, setDay } = usePlan(asUser);

  // Phase 4: harvest-ready garden produce for "Fresh in Garden" banner.
  const gardenReadyQuery = useQuery({
    queryKey: ['garden-harvest-ready'],
    queryFn: () => api.gardenHarvestReady(),
    retry: false,
    staleTime: 60_000,
  });
  const readyIngredients = gardenReadyQuery.data?.ready ?? [];

  // Phase 4: backend word-boundary recipe matches against garden produce.
  const gardenMatchesQuery = useQuery({
    queryKey: ['garden-recipe-matches'],
    queryFn: () => api.gardenRecipeMatches(),
    retry: false,
    staleTime: 60_000,
  });
  const gardenMatches: RecipeMatch[] = gardenMatchesQuery.data?.matches ?? [];
  const gardenMatchPaths = useMemo(
    () => new Set(gardenMatches.map((m) => m.path)),
    [gardenMatches],
  );

  // Phase 4: draft review modal state.
  const [draftOpen, setDraftOpen] = useState(false);
  const [draftRecipe, setDraftRecipe] = useState<RecipeDraftResponse | null>(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [freshInGarden, setFreshInGarden] = useState(false);

  const go = (next: Record<string, string | null>) => {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null) merged.delete(key);
      else merged.set(key, value);
    }
    setParams(merged);
    window.scrollTo?.({ top: 0 });
  };
  const home = () => go({ recipe: null, edit: null, add: null });
  const refreshCatalog = () => void queryClient.invalidateQueries({ queryKey: ['recipe-catalog'] });

  const draftRecipeFromGarden = async () => {
    const ingredients = readyIngredients.flatMap((r) => r.culinary_aliases ?? []);
    const focusCrop = readyIngredients[0]?.common_name ?? '';
    setDraftBusy(true);
    try {
      const draft = await api.gardenRecipeDraft({
        ingredients: Array.from(new Set(ingredients)),
        focus_crop: focusCrop,
        servings: 7,
      });
      setDraftRecipe(draft);
      setDraftOpen(true);
    } catch {
      toast.error('Could not draft recipe from garden produce.');
    } finally {
      setDraftBusy(false);
    }
  };

  const saveDraftToCookbook = async () => {
    if (!draftRecipe) return;
    setDraftBusy(true);
    try {
      const focus = draftRecipe.focus_crop || draftRecipe.ingredients[0] || 'Garden';
      const saved = await api.cookbookSave({
        title: `${focus} Garden Recipe`,
        content: draftRecipe.markdown,
      });
      if (saved.status === 'SUCCESS') {
        toast.success('Saved to the family cookbook');
        setDraftOpen(false);
        setDraftRecipe(null);
        refreshCatalog();
        void queryClient.invalidateQueries({ queryKey: ['recipe-sources'] });
        const savedPath = (saved.detail as { path?: string } | undefined)?.path;
        if (savedPath) void queryClient.invalidateQueries({ queryKey: ['recipe-body', savedPath] });
      } else {
        toast.error(saved.message || 'Could not save the draft');
      }
    } catch {
      toast.error('Could not save the draft');
    } finally {
      setDraftBusy(false);
    }
  };

  const openRecipe = recipes.find((recipe) => recipe.path === openPath) ?? null;
  const editRecipe = editing && editing !== 'new' ? recipes.find((recipe) => recipe.path === editing) ?? null : null;
  const editBody = editRecipe ? (queryClient.getQueryData(['recipe-body', editRecipe.path]) as ExecutionResponse | undefined) : undefined;

  const afterSave = (path: string | null) => {
    refreshCatalog();
    void queryClient.invalidateQueries({ queryKey: ['recipe-sources'] });
    void queryClient.invalidateQueries({ queryKey: ['recipe-suggestions'] });
    if (path) void queryClient.invalidateQueries({ queryKey: ['recipe-body', path] });
    go({ edit: null, add: null, tab: null, recipe: path });
  };

  const adopt = async () => {
    setBusy('adopt');
    try {
      const res = await api.cookbookAdopt();
      if (res.status === 'SUCCESS') toast.success(res.message || 'Moved into the family cookbook');
      else toast.error(res.message || 'Could not move your recipes');
      refreshCatalog();
      void queryClient.invalidateQueries({ queryKey: ['recipe-personal'] });
    } catch {
      toast.error('Could not move your recipes');
    } finally {
      setBusy(null);
    }
  };

  const teachJarvis = async () => {
    setMenuOpen(false);
    setBusy('teach');
    try {
      const res = await api.syncNotesRag({ cookbook: true });
      if (res.status === 'SUCCESS') toast.success('Jarvis is learning the recipes');
      else toast.error(res.message || 'Could not sync the recipes');
    } catch {
      toast.error('Could not sync the recipes');
    } finally {
      setBusy(null);
    }
  };

  const findPhotos = async () => {
    setMenuOpen(false);
    try {
      const res = await api.findRecipePhotos();
      toast.success(res.message || 'Looking for photos in the background');
    } catch {
      toast.error('Could not start looking for photos');
    }
  };

  let content: React.ReactNode;
  if (adding) {
    content = (
      <AddRecipe
        existingTitles={recipes.map((r) => r.title)}
        asUser={asUser}
        onSaved={afterSave}
        onWriteOwn={() => go({ add: null, edit: 'new' })}
        onLog={(title, body) => setLogRequest({ title, path: '', body })}
        onBack={home}
      />
    );
  } else if (editing === 'new' || editRecipe) {
    content = (
      <RecipeEditor
        key={editing}
        path={editRecipe?.path}
        initialTitle={editRecipe?.title ?? ''}
        initialBody={editBody?.status === 'SUCCESS' ? editBody.message ?? '' : ''}
        canChange={editRecipe ? editRecipe.can_change : true}
        ownerLabel={editRecipe?.owner_label}
        etag={(editBody?.detail as { etag?: string } | undefined)?.etag ?? undefined}
        asUser={asUser}
        onSaved={(path) => (editRecipe && !editRecipe.can_change ? go({ edit: null }) : afterSave(path))}
        onCancel={() => go({ edit: null })}
      />
    );
  } else if (openRecipe) {
    content = (
      <RecipeView
        key={openRecipe.path}
        recipe={openRecipe}
        plan={plan}
        onPlan={(day, title) => setDay(day, title)}
        onBack={() => go({ recipe: null })}
        onEdit={() => go({ edit: openRecipe.path })}
        onStatsChanged={refreshCatalog}
        onDeleted={() => {
          refreshCatalog();
          go({ recipe: null });
        }}
        onShare={setShare}
        onLog={(title, path, body) => setLogRequest({ title, path, body })}
        skylightOff={skylightOff}
        kid={kid}
      />
    );
  } else if (tab === 'week') {
    content = (
      <WeekPlan
        plan={plan}
        save={savePlan}
        recipes={recipes}
        asUser={asUser}
        skylightOff={skylightOff}
        onOpen={(recipe) => go({ recipe: recipe.path })}
        onShare={setShare}
        onLog={(title, path, body, day) => setLogRequest({ title, path, body, planDay: day })}
        onListBuilt={() => void queryClient.invalidateQueries({ queryKey: ['shopping-list'] })}
      />
    );
  } else if (tab === 'list') {
    content = <ShoppingList asUser={asUser} skylightOff={skylightOff} onShare={setShare} onGoToWeek={() => go({ tab: 'week' })} />;
  } else {
    content = (
      <RecipeBrowser
        recipes={recipes}
        me={catalog.data?.me ?? me?.username ?? ''}
        loading={catalog.isLoading}
        error={catalog.isError ? errorText(catalog.error, 'Could not open the cookbook') : null}
        onRetry={() => void catalog.refetch()}
        onOpen={(recipe) => go({ recipe: recipe.path })}
        freshInGarden={freshInGarden}
        gardenMatchPaths={gardenMatchPaths}
      />
    );
  }

  const inDetail = adding || Boolean(editing) || Boolean(openRecipe);
  return (
    <div className="space-y-4" data-testid="recipes">
      {!inDetail && (
        <header className="space-y-3">
          <div className="relative flex flex-wrap items-center gap-3">
            <div className="mr-auto flex items-center gap-2">
              <BookOpen size={20} className="text-orange-300" />
              <h1 className="text-xl font-bold text-white">Family cookbook</h1>
              <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-400/10 px-2 py-0.5 text-[10px] text-amber-200">
                <Users size={11} /> shared
              </span>
            </div>
            {suggestions.length > 0 && (
              <button
                type="button"
                onClick={() => setSuggestionsOpen((open) => !open)}
                aria-pressed={suggestionsOpen}
                data-testid="recipe-suggestions-toggle"
                className={`glass-button min-h-11 px-3 text-xs ${toReview > 0 ? 'ring-1 ring-amber-400/60' : ''}`}
              >
                <Inbox size={14} /> {toReview > 0 ? `${toReview} to review` : `${suggestions.length} suggested`}
              </button>
            )}
            <div className="flex items-center gap-2">
            <button type="button" onClick={() => go({ add: '1' })} className="glass-button min-h-11 bg-purple-500/20 px-4 text-sm font-semibold text-white">
              <Plus size={16} /> <span className="sm:hidden">Add</span><span className="hidden sm:inline">Add a recipe</span>
            </button>
            {/* On a phone the menu lines up with the header's right edge (the button can sit anywhere in the
                wrapped row, and a menu hung from it ran off one side or the other). */}
            <div className="relative max-sm:static">
              <button type="button" onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen} aria-label="More cookbook actions" className="glass-button flex h-11 w-11 items-center justify-center p-0">
                {busy === 'teach' ? <Loader2 size={14} className="animate-spin" /> : <MoreHorizontal size={16} />}
              </button>
              {menuOpen && (
                <div className="absolute right-0 z-30 mt-1 w-64 space-y-1 rounded-xl border border-white/10 bg-slate-900/95 p-2 shadow-xl backdrop-blur" role="menu">
                  <button type="button" role="menuitem" onClick={() => void teachJarvis()} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-xs text-slate-200 hover:bg-white/10">
                    <BookOpen size={14} /> Teach Jarvis the recipes
                  </button>
                  {isAdmin && (
                    <button type="button" role="menuitem" onClick={() => void findPhotos()} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-xs text-slate-200 hover:bg-white/10">
                      <ImagePlus size={14} /> Find missing photos
                    </button>
                  )}
                  {isAdmin && (
                    <div className="border-t border-white/10 px-1 pt-2">
                      <p className="mb-1 text-[10px] text-slate-500">Week plan, list and new recipes as</p>
                      <SendAsSelector value={sendAs} onChange={setSendAs} />
                    </div>
                  )}
                </div>
              )}
            </div>
            </div>
          </div>

          <nav className="flex gap-1 rounded-2xl border border-white/5 bg-white/[0.03] p-1" aria-label="Cookbook sections">
            {TABS.map(([key, label, Icon]) => (
              <button
                key={key}
                type="button"
                onClick={() => go({ tab: key === 'recipes' ? null : key })}
                aria-current={tab === key ? 'page' : undefined}
                className={`flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-xl text-sm transition ${
                  tab === key ? 'bg-purple-500/25 font-semibold text-white' : 'text-slate-400 hover:text-white'
                }`}
              >
                <Icon size={15} className="hidden min-[400px]:block" /> {label}
              </button>
            ))}
          </nav>

          {personal.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-sky-400/30 bg-sky-400/10 px-4 py-2" data-testid="recipe-personal">
              <span className="mr-auto text-xs text-sky-100">
                {personal.length} of your recipe{personal.length === 1 ? ' is' : 's are'} still only in your own folder.
              </span>
              <button type="button" onClick={() => void adopt()} disabled={busy === 'adopt'} className="glass-button min-h-11 px-3 text-xs disabled:opacity-60">
                {busy === 'adopt' ? <Loader2 size={14} className="animate-spin" /> : <Users size={14} />}
                Add them to the family cookbook
              </button>
            </div>
          )}
          {readyIngredients.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-emerald-400/30 bg-emerald-400/10 px-4 py-2" data-testid="recipe-fresh-in-garden">
              <Scissors size={14} className="text-emerald-400" />
              <span className="mr-auto text-xs text-emerald-100">
                Fresh in garden:{' '}
                {readyIngredients.map((r) => r.nickname || r.common_name).join(', ')}
              </span>
              <button
                type="button"
                onClick={() => void draftRecipeFromGarden()}
                disabled={draftBusy}
                className={`glass-button min-h-11 px-3 text-xs disabled:opacity-60 ${
                  freshInGarden ? 'text-emerald-400 ring-1 ring-emerald-400/60' : 'text-emerald-300 hover:text-emerald-400'
                }`}
              >
                {draftBusy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                Draft new family recipe
              </button>
              <button
                type="button"
                onClick={() => setFreshInGarden((open) => !open)}
                aria-pressed={freshInGarden}
                className={`glass-button min-h-11 px-3 text-xs disabled:opacity-60 ${
                  freshInGarden ? 'text-emerald-400 ring-1 ring-emerald-400/60' : 'text-emerald-300 hover:text-emerald-400'
                }`}
              >
                {freshInGarden ? 'Hide garden matches' : 'Use from garden'}
              </button>
            </div>
          )}
          {suggestionsOpen && suggestions.length > 0 && (
            <SuggestionsPanel
              suggestions={suggestions}
              onApproved={(path) => {
                refreshCatalog();
                void queryClient.invalidateQueries({ queryKey: ['recipe-body', path] });
              }}
            />
          )}
        </header>
      )}

      {content}

      {share && (
        <ShareToChatSheet
          reference={share.reference}
          text={share.text}
          heading={share.heading}
          previewIcon="🍽"
          buildMessage={share.buildMessage}
          onClose={() => setShare(null)}
        />
      )}
      {logRequest && (
        <LogRecipeSheet
          title={logRequest.title}
          path={logRequest.path}
          body={logRequest.body}
          planDay={logRequest.planDay}
          asUser={asUser}
          onMarkedCooked={() => void queryClient.invalidateQueries({ queryKey: ['meal-plan'] })}
          onClose={() => setLogRequest(null)}
        />
      )}
      {draftOpen && draftRecipe && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setDraftOpen(false)}>
          <div className="bg-white/10 border border-white/20 rounded-2xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-xl font-bold text-white">Draft recipe from garden</h2>
              <button type="button" onClick={() => setDraftOpen(false)} className="text-slate-400 hover:text-white">
                <X size={20} />
              </button>
            </div>
            <div className="space-y-4">
              <p className="text-sm text-slate-300">{draftRecipe.note}</p>
              <p className="text-xs text-slate-500">Serves {draftRecipe.servings} · {draftRecipe.ingredients.join(', ')}</p>
              <div className="h-48 overflow-y-auto text-sm text-slate-200 whitespace-pre-wrap font-mono bg-slate-900/50 rounded-lg p-3 border border-white/10">
                {draftRecipe.markdown}
              </div>
              <div className="mt-4 flex gap-3">
                <button
                  type="button"
                  onClick={() => { setDraftOpen(false); setDraftRecipe(null); }}
                  className="flex-1 px-4 py-2.5 rounded-lg bg-white/5 text-slate-300 hover:bg-white/10 border border-white/10 disabled:opacity-50"
                >
                  Dismiss
                </button>
                <button
                  type="button"
                  onClick={() => void saveDraftToCookbook()}
                  disabled={draftBusy}
                  className="flex-1 px-4 py-2.5 rounded-lg bg-emerald-500 text-white hover:bg-emerald-400 disabled:opacity-50"
                >
                  Save to Cookbook
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
