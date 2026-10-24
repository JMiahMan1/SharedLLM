import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Camera,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Plus,
  Search,
  Trash2,
  Utensils,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '../../services/api';
import {
  MEAL_LABELS,
  MEAL_ORDER,
  calorieSummary,
  formatDayLabel,
  localDay,
  macroPercent,
  nutritionProgress,
  shiftDay,
  targetLabel,
  type Meal,
} from '../../lib/healthHub';
import { isKidMode } from '../../lib/healthSwitches';
import { foodEntryFromSearch } from '../../lib/foodEntry';
import { errorText } from '../../lib/errorText';
import VoiceFoodDraft from './VoiceFoodDraft';
import type { FoodEntry, FoodLabelDraft, FoodSearchItem } from '../../types/api';
import type { HealthTab } from './HealthTabs';

interface Props {
  onNavigate: (tab: HealthTab) => void;
}

type AddMode = 'search' | 'manual' | 'barcode' | 'photo' | 'voice';

const EMPTY_MANUAL = {
  name: '',
  brand: '',
  calories: '',
  protein: '',
  carbs: '',
  fat: '',
  servings: '1',
};

function MacroBar({ label, consumed, target }: { label: string; consumed: number; target: number | null }) {
  const percent = macroPercent(consumed, target);
  return (
    <div>
      <div className="flex items-baseline justify-between text-[10px]">
        <span className="text-slate-500">{label}</span>
        <span className="tabular-nums text-slate-400">
          {Math.round(consumed)}g{target ? ` / ${target}g` : ''}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <div className="h-full rounded-full bg-emerald-500" style={{ width: `${percent ?? 0}%` }} />
      </div>
    </div>
  );
}

function densityTone(band: 'green' | 'yellow' | 'orange'): string {
  if (band === 'green') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300';
  if (band === 'yellow') return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
  return 'border-orange-500/30 bg-orange-500/10 text-orange-300';
}

export default function HealthNutrition({ onNavigate }: Props) {
  const queryClient = useQueryClient();
  const [day, setDay] = useState(localDay());
  const [addingMeal, setAddingMeal] = useState<Meal | null>(null);
  const [mode, setMode] = useState<AddMode>('search');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<FoodSearchItem[] | null>(null);
  const [offlineSources, setOfflineSources] = useState<string[]>([]);
  const [selected, setSelected] = useState<FoodSearchItem | null>(null);
  const [grams, setGrams] = useState('');
  const [servings, setServings] = useState('1');
  const [barcode, setBarcode] = useState('');
  const [manual, setManual] = useState(EMPTY_MANUAL);
  // Set when the By hand form was filled from a label photo: the raw text and
  // what the label did not print, shown beside the draft until it is saved.
  const [photoRead, setPhotoRead] = useState<FoodLabelDraft | null>(null);

  const nutritionQuery = useQuery({
    queryKey: ['health-nutrition', day],
    queryFn: () => api.getNutritionDay(day),
    staleTime: 30_000,
  });
  // Kid mode never shows calorie numbers, ranges or density colours.
  const profileQuery = useQuery({ queryKey: ['health-profile'], queryFn: () => api.getHealthProfile() });
  const kid = isKidMode(profileQuery.data);
  const nutrition = nutritionQuery.data;
  const target = nutrition?.target;
  const progress = nutritionProgress(nutrition?.totals.calories ?? 0, target);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['health-nutrition'] });
    void queryClient.invalidateQueries({ queryKey: ['health-summary'] });
    void queryClient.invalidateQueries({ queryKey: ['health-food'] });
  };

  const resetAdd = () => {
    setAddingMeal(null);
    setMode('search');
    setQuery('');
    setResults(null);
    setOfflineSources([]);
    setSelected(null);
    setGrams('');
    setServings('1');
    setBarcode('');
    setManual(EMPTY_MANUAL);
    setPhotoRead(null);
  };

  const search = useMutation({
    mutationFn: (text: string) => api.searchFood(text, 10),
    onSuccess: (data) => {
      setResults(data.results);
      setOfflineSources(data.offline_sources ?? []);
      if (data.results.length === 0) toast('No matches. Try a different wording or add it by hand.');
    },
    onError: (error: Error) => toast.error(error.message || 'Food search failed'),
  });

  const lookup = useMutation({
    mutationFn: (code: string) => api.lookupBarcode(code),
    onSuccess: (product) => {
      setSelected(product);
      setGrams(product.serving_grams ? String(product.serving_grams) : '100');
      setServings('1');
    },
    onError: (error: Error) => toast.error(error.message || 'Barcode lookup failed'),
  });

  const readPhoto = useMutation({
    mutationFn: (photo: File) => api.readFoodLabelPhoto(photo),
    onSuccess: (read) => {
      if (read.found.length === 0) {
        toast.error('No nutrition label could be read in that photo. Try a sharper, closer photo or type it.');
        return;
      }
      const value = (number: number | null) => (number === null ? '' : String(number));
      setManual({
        ...EMPTY_MANUAL,
        calories: value(read.draft.calories),
        protein: value(read.draft.protein_g),
        carbs: value(read.draft.carbs_g),
        fat: value(read.draft.fat_g),
      });
      setPhotoRead(read);
      setMode('manual');
    },
    onError: (error) => toast.error(errorText(error, 'The photo could not be read. Type the values instead.')),
  });

  const addFood = useMutation({
    mutationFn: () => {
      if (!addingMeal) throw new Error('Choose a meal first');
      if (selected) {
        return api.addFood(
          foodEntryFromSearch(selected, {
            grams: Number(grams) || selected.serving_grams || 100,
            servings: Number(servings) || 1,
            day,
            meal: addingMeal,
          }),
        );
      }
      const name = manual.name.trim();
      if (!name) throw new Error('Give the food a name');
      return api.addFood({
        day,
        meal: addingMeal,
        name,
        brand: manual.brand.trim(),
        calories: Number(manual.calories) || 0,
        protein_g: Number(manual.protein) || 0,
        carbs_g: Number(manual.carbs) || 0,
        fat_g: Number(manual.fat) || 0,
        servings: Number(manual.servings) || 1,
        serving_grams: photoRead?.draft.serving_grams ?? null,
        fiber_g: photoRead?.draft.fiber_g ?? null,
        sugar_g: photoRead?.draft.sugar_g ?? null,
        sodium_mg: photoRead?.draft.sodium_mg ?? null,
        source: photoRead ? 'photo' : 'manual',
      });
    },
    onSuccess: () => {
      toast.success('Logged');
      resetAdd();
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not log that food'),
  });

  const removeFood = useMutation({
    mutationFn: (id: number) => api.deleteFood(id),
    onSuccess: () => {
      toast.success('Entry removed');
      invalidate();
    },
    onError: (error: Error) => toast.error(error.message || 'Could not remove that entry'),
  });

  const selectedPreview = selected?.per_100g.calories
    ? Math.round(
        selected.per_100g.calories *
          ((Number(grams) || selected.serving_grams || 100) / 100) *
          (Number(servings) || 1),
      )
    : null;

  return (
    <div className="space-y-3 sm:space-y-4" data-testid="health-nutrition">
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setDay(shiftDay(day, -1))}
          aria-label="Previous day"
          className="glass-button flex h-10 w-10 items-center justify-center px-0 pointer-coarse:h-11 pointer-coarse:w-11"
        >
          <ChevronLeft size={16} />
        </button>
        <div className="text-center">
          <p className="text-sm font-semibold text-white">{formatDayLabel(day)}</p>
          <button
            type="button"
            onClick={() => setDay(localDay())}
            className="text-[10px] text-purple-300 underline underline-offset-2"
          >
            Today
          </button>
        </div>
        <button
          type="button"
          onClick={() => setDay(shiftDay(day, 1))}
          aria-label="Next day"
          className="glass-button flex h-10 w-10 items-center justify-center px-0 pointer-coarse:h-11 pointer-coarse:w-11"
        >
          <ChevronRight size={16} />
        </button>
      </div>

      <section
        className="glass-panel rounded-2xl border border-white/5 p-4 sm:p-5"
        aria-labelledby="nutrition-summary-heading"
        data-testid="nutrition-summary"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2
              id="nutrition-summary-heading"
              className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400"
            >
              <Utensils size={13} className="text-emerald-300" />
              {target?.kind === 'teen_guidance' && !kid ? 'Guidance for today' : 'Today'}
            </h2>
            {kid ? (
              <p className="mt-1 text-2xl font-bold text-white" data-testid="nutrition-kid-summary">
                {!nutrition
                  ? 'Loading…'
                  : nutrition.logged
                    ? `${nutrition.entries_count} ${nutrition.entries_count === 1 ? 'thing' : 'things'} logged`
                    : 'Nothing logged yet'}
              </p>
            ) : (
              <>
                <p className="mt-1 text-3xl font-bold tabular-nums text-white">
                  {Math.round(nutrition?.totals.calories ?? 0).toLocaleString()}
                  <span className="ml-1 text-sm font-medium text-slate-500">kcal</span>
                </p>
                <p className="text-[11px] text-slate-500">{nutrition ? calorieSummary(nutrition) : 'Loading…'}</p>
              </>
            )}
          </div>
          {!kid && (
            <div className="text-right">
              <p className="text-[11px] text-slate-400">{targetLabel(target)}</p>
              {target?.kind === 'teen_guidance' && (
                <p className="mt-1 max-w-[11rem] text-[10px] text-slate-500">
                  A wide range for growing bodies. No deficit, ever.
                </p>
              )}
            </div>
          )}
        </div>

        {!kid && (
          <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className={`h-full rounded-full ${progress.over ? 'bg-amber-500' : 'bg-emerald-500'}`}
              style={{ width: `${progress.percent ?? 0}%` }}
            />
          </div>
        )}

        {kid ? (
          <p className="mt-3 text-xs text-slate-400">
            Eat a rainbow, drink water, and stop when you feel full. Logging is just for noticing.
          </p>
        ) : target?.kind === 'unavailable' ? (
          <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
            <p className="text-xs text-slate-300">
              A range needs {target.missing.length ? target.missing.join(', ') : 'profile details'}.
            </p>
            <button
              type="button"
              onClick={() => onNavigate('health')}
              className="mt-2 text-[11px] font-semibold text-purple-300 underline underline-offset-2"
            >
              Set up your health profile
            </button>
          </div>
        ) : (
          <div className="mt-3 grid grid-cols-3 gap-3">
            <MacroBar label="Protein" consumed={nutrition?.totals.protein_g ?? 0} target={target?.protein_g ?? null} />
            <MacroBar label="Carbs" consumed={nutrition?.totals.carbs_g ?? 0} target={target?.carbs_g ?? null} />
            <MacroBar label="Fat" consumed={nutrition?.totals.fat_g ?? 0} target={target?.fat_g ?? null} />
          </div>
        )}
        {nutrition && nutrition.logged && (
          <p className="mt-3 text-[10px] text-slate-500">
            {nutrition.entries_count} {nutrition.entries_count === 1 ? 'entry' : 'entries'} ·{' '}
            Protein {Math.round(nutrition.totals.protein_g)}g · Carbs {Math.round(nutrition.totals.carbs_g)}g · Fat{' '}
            {Math.round(nutrition.totals.fat_g)}g
          </p>
        )}
      </section>

      {addingMeal && (
        <section className="glass-panel rounded-2xl border border-purple-500/20 p-4" data-testid="add-food-panel">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-white">Add to {MEAL_LABELS[addingMeal]}</h3>
            <button type="button" onClick={resetAdd} className="text-[11px] text-slate-400 hover:text-white">
              Cancel
            </button>
          </div>

          <div className="mt-3 flex gap-1" role="tablist" aria-label="How to add food">
            {([
              ['search', 'Search'],
              ['manual', 'By hand'],
              ['barcode', 'Barcode'],
              ['photo', 'Photo'],
              ['voice', 'Voice'],
            ] as Array<[AddMode, string]>).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={mode === id}
                onClick={() => {
                  setMode(id);
                  setSelected(null);
                  if (id !== 'manual') setPhotoRead(null);
                }}
                className={`min-h-9 rounded-lg px-3 text-xs font-semibold ${
                  mode === id
                    ? 'bg-purple-500/20 text-purple-200 border border-purple-500/30'
                    : 'text-slate-400 border border-transparent hover:text-white'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {mode === 'search' && !selected && (
            <>
              <form
                className="mt-3 flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (query.trim()) search.mutate(query.trim());
                }}
              >
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Oats, yoghurt, bread…"
                  aria-label="Search foods"
                  className="glass-input h-10 min-w-0 flex-1 px-3 text-sm pointer-coarse:h-11"
                />
                <button
                  type="submit"
                  disabled={search.isPending || !query.trim()}
                  className="glass-button flex min-h-10 items-center gap-1.5 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-11"
                >
                  {search.isPending ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                  Search
                </button>
              </form>
              {results && (
                <div className="mt-2 divide-y divide-white/5" data-testid="food-search-results">
                  {offlineSources.includes('openfoodfacts') && (
                    <p className="py-2 text-[10px] text-amber-300" data-testid="food-search-offline">
                      Branded products are offline right now — generic foods still work.
                    </p>
                  )}
                  {results.length === 0 ? (
                    <p className="py-3 text-xs text-slate-500">
                      No matches. Try a different wording, or add it by hand.
                    </p>
                  ) : (
                    results.map((item) => (
                      <button
                        key={item.code}
                        type="button"
                        onClick={() => {
                          setSelected(item);
                          setGrams(item.serving_grams ? String(item.serving_grams) : '100');
                          setServings('1');
                        }}
                        className="flex w-full items-center gap-3 py-2.5 text-left"
                      >
                        {item.image ? (
                          <img src={item.image} alt="" className="h-9 w-9 rounded-lg object-cover" />
                        ) : (
                          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/[0.05] text-slate-500">
                            <Utensils size={14} />
                          </span>
                        )}
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-xs font-medium text-slate-200">{item.name}</span>
                          <span className="block truncate text-[10px] text-slate-500">
                            {item.source === 'usda_fdc' ? (item.brand ? `USDA · ${item.brand}` : 'USDA') : item.brand}
                            {kid ? '' : item.per_100g.calories !== null ? ` · ${Math.round(item.per_100g.calories)} kcal / 100g` : ' · no calorie data'}
                          </span>
                        </span>
                        <Plus size={15} className="shrink-0 text-purple-300" />
                      </button>
                    ))
                  )}
                </div>
              )}
            </>
          )}

          {mode === 'search' && selected && (
            <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.03] p-3" data-testid="food-portion-panel">
              <p className="text-sm font-semibold text-white">{selected.name}</p>
              <p className="text-[11px] text-slate-500">
                {selected.brand}
                {!kid && selected.per_100g.calories !== null ? ` · ${Math.round(selected.per_100g.calories)} kcal / 100g` : ''}
              </p>
              {selected.portions && selected.portions.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5" data-testid="food-portion-chips">
                  {selected.portions.slice(0, 8).map((portion) => (
                    <button
                      key={`${portion.label}-${portion.grams}`}
                      type="button"
                      onClick={() => setGrams(String(portion.grams))}
                      className="rounded-full border border-white/10 bg-white/[0.05] px-2 py-1 text-[10px] text-slate-300"
                    >
                      {portion.label}
                    </button>
                  ))}
                </div>
              )}
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label className="text-[11px] text-slate-500">
                  Portion (g)
                  <input
                    type="number"
                    min={1}
                    value={grams}
                    onChange={(event) => setGrams(event.target.value)}
                    className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
                  />
                </label>
                <label className="text-[11px] text-slate-500">
                  Servings
                  <input
                    type="number"
                    min={0.1}
                    step={0.1}
                    value={servings}
                    onChange={(event) => setServings(event.target.value)}
                    className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
                  />
                </label>
              </div>
              <div className="mt-2 flex items-center justify-between">
                <span className="text-xs text-slate-400">
                  {kid ? '' : selectedPreview !== null ? `≈ ${selectedPreview} kcal` : 'No calorie data'}
                </span>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setSelected(null)} className="text-[11px] text-slate-400">
                    Back
                  </button>
                  <button
                    type="button"
                    onClick={() => addFood.mutate()}
                    disabled={addFood.isPending}
                    data-testid="confirm-add-food"
                    className="glass-button min-h-10 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-11"
                  >
                    {addFood.isPending ? 'Adding…' : 'Add'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {mode === 'photo' && (
            <div className="mt-3 space-y-2" data-testid="photo-food-panel">
              <p className="text-xs text-slate-400">
                Photograph the nutrition label. The numbers it prints fill the form for you to check; anything it
                cannot read stays empty.
              </p>
              <label className="glass-button flex min-h-11 cursor-pointer items-center justify-center gap-2 text-xs font-semibold">
                {readPhoto.isPending ? <Loader2 size={14} className="animate-spin" /> : <Camera size={14} />}
                {readPhoto.isPending ? 'Reading the label…' : 'Take or choose a photo'}
                <input
                  type="file"
                  accept="image/*"
                  capture="environment"
                  className="sr-only"
                  aria-label="Nutrition label photo"
                  disabled={readPhoto.isPending}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = '';
                    if (file) readPhoto.mutate(file);
                  }}
                />
              </label>
            </div>
          )}

          {mode === 'voice' && addingMeal && (
            <VoiceFoodDraft
              day={day}
              meal={addingMeal}
              kid={kid}
              onLogged={() => {
                resetAdd();
                invalidate();
              }}
            />
          )}

          {mode === 'manual' && photoRead && (
            <div className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/[0.06] p-3 text-[11px] text-amber-100" data-testid="photo-draft-note">
              <p>
                Read from your photo. Check every number and give it a name before adding.
                {photoRead.missing.length > 0 &&
                  ` Not on the label: ${photoRead.missing.map((field) => field.replace('_g', '')).join(', ')}.`}
                {photoRead.per_100g && ' These values are per 100 g: set Servings to the portion you ate (e.g. 0.5 for 50 g).'}
                {photoRead.draft.serving_label && ` Serving: ${photoRead.draft.serving_label}.`}
              </p>
              <details className="mt-1 text-slate-300">
                <summary className="cursor-pointer">What the photo said</summary>
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-[10px]">{photoRead.text}</pre>
              </details>
            </div>
          )}

          {mode === 'manual' && (
            <div className="mt-3 space-y-2" data-testid="manual-food-panel">
              <input
                type="text"
                value={manual.name}
                onChange={(event) => setManual({ ...manual, name: event.target.value })}
                placeholder="Food name"
                className="glass-input h-10 w-full px-3 text-sm pointer-coarse:h-11"
              />
              <input
                type="text"
                value={manual.brand}
                onChange={(event) => setManual({ ...manual, brand: event.target.value })}
                placeholder="Brand (optional)"
                className="glass-input h-10 w-full px-3 text-xs pointer-coarse:h-11"
              />
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {([
                  ['calories', 'Calories'],
                  ['protein', 'Protein g'],
                  ['carbs', 'Carbs g'],
                  ['fat', 'Fat g'],
                ] as Array<[keyof typeof manual, string]>).map(([key, label]) => (
                  <label key={key} className="text-[11px] text-slate-500">
                    {label}
                    <input
                      type="number"
                      min={0}
                      value={manual[key]}
                      onChange={(event) => setManual({ ...manual, [key]: event.target.value })}
                      className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
                    />
                  </label>
                ))}
              </div>
              <div className="flex items-end gap-2">
                <label className="w-24 text-[11px] text-slate-500">
                  Servings
                  <input
                    type="number"
                    min={0.1}
                    step={0.1}
                    value={manual.servings}
                    onChange={(event) => setManual({ ...manual, servings: event.target.value })}
                    className="glass-input mt-1 h-10 w-full px-2 text-xs pointer-coarse:h-11"
                  />
                </label>
                <button
                  type="button"
                  onClick={() => addFood.mutate()}
                  disabled={addFood.isPending}
                  className="glass-button ml-auto min-h-10 px-4 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-11"
                >
                  {addFood.isPending ? 'Adding…' : 'Add food'}
                </button>
              </div>
            </div>
          )}

          {mode === 'barcode' && !selected && (
            <form
              className="mt-3 flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (barcode.trim()) lookup.mutate(barcode.trim());
              }}
            >
              <input
                type="text"
                inputMode="numeric"
                value={barcode}
                onChange={(event) => setBarcode(event.target.value)}
                placeholder="Barcode number"
                aria-label="Barcode"
                className="glass-input h-10 min-w-0 flex-1 px-3 text-sm pointer-coarse:h-11"
              />
              <button
                type="submit"
                disabled={lookup.isPending || !barcode.trim()}
                className="glass-button min-h-10 px-3 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-11"
              >
                {lookup.isPending ? 'Looking…' : 'Look up'}
              </button>
            </form>
          )}

          {mode === 'barcode' && selected && (
            <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
              <p className="text-sm font-semibold text-white">{selected.name}</p>
              <p className="text-[11px] text-slate-500">{selected.brand}</p>
              <div className="mt-2 flex items-end gap-2">
                <label className="text-[11px] text-slate-500">
                  Portion (g)
                  <input
                    type="number"
                    min={1}
                    value={grams}
                    onChange={(event) => setGrams(event.target.value)}
                    className="glass-input mt-1 h-10 w-24 px-2 text-xs pointer-coarse:h-11"
                  />
                </label>
                <button
                  type="button"
                  onClick={() => addFood.mutate()}
                  disabled={addFood.isPending}
                  className="glass-button ml-auto min-h-10 px-4 text-xs font-semibold disabled:opacity-50 pointer-coarse:min-h-11"
                >
                  Add
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      {MEAL_ORDER.map((meal) => {
        const entries = nutrition?.meals[meal]?.entries ?? [];
        return (
          <section
            key={meal}
            className="glass-panel rounded-2xl border border-white/5 p-4"
            aria-labelledby={`meal-${meal}`}
            data-testid={`meal-${meal}`}
          >
            <div className="flex items-center justify-between gap-3">
              <h3 id={`meal-${meal}`} className="text-sm font-semibold text-white">
                {MEAL_LABELS[meal]}
              </h3>
              <button
                type="button"
                onClick={() => setAddingMeal(meal)}
                className="flex min-h-9 items-center gap-1 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 text-[11px] font-semibold text-slate-300 hover:text-white pointer-coarse:min-h-11"
              >
                <Plus size={12} /> Add
              </button>
            </div>
            {entries.length === 0 ? (
              <p className="mt-1 text-[11px] text-slate-500">Nothing logged.</p>
            ) : (
              <div className="mt-1 divide-y divide-white/5">
                {entries.map((entry: FoodEntry) => (
                  <div key={entry.id} className="flex items-center gap-2 py-2.5" data-testid={`food-row-${entry.id}`}>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium text-slate-200">
                        {entry.name}
                        {entry.brand ? <span className="ml-1 text-slate-500">· {entry.brand}</span> : null}
                      </p>
                      <p className="text-[10px] text-slate-500">
                        {kid
                          ? entry.servings !== 1
                            ? `${entry.servings} servings`
                            : '1 serving'
                          : `${Math.round(entry.calories * entry.servings)} kcal${entry.servings !== 1 ? ` · ${entry.servings} servings` : ''} · P ${Math.round(entry.protein_g * entry.servings)}g C ${Math.round(entry.carbs_g * entry.servings)}g F ${Math.round(entry.fat_g * entry.servings)}g`}
                      </p>
                    </div>
                    {entry.density && !kid && (
                      <span className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[9px] font-semibold ${densityTone(entry.density.band)}`}>
                        {entry.density.band}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => removeFood.mutate(entry.id)}
                      disabled={removeFood.isPending}
                      aria-label={`Remove ${entry.name}`}
                      className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50 pointer-coarse:h-11 pointer-coarse:w-11"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}

      {nutrition && !nutrition.logged && (
        <p className="px-1 text-center text-[11px] text-slate-500">
          Nothing logged for this day. Adding food here never infers a zero-calorie day — it records
          what you enter.
        </p>
      )}
    </div>
  );
}
