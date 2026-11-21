import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import {
  AlertTriangle, CalendarPlus, Check, Compass, Leaf, Plus,
  RotateCw, Search, Sprout, Trash2, X,
} from 'lucide-react';
import { api } from '../../services/api';
import { HomeHubBindingPanel } from './HomeHubBindingPanel';
import type {
  GardenLocation, GardenPlanOut, GardenProfile, GardenSpecies, PantryGoalCalcResponse,
  PantryGoalOut, PlanCellIn, PlannerSizingResponse,
} from '../../types/api';

const inputClass =
  'w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-emerald-500 text-sm pointer-coarse:min-h-11';
const labelClass = 'flex flex-col gap-1 text-[10px] text-slate-400';
const buttonClass =
  'px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10 text-xs transition-colors disabled:opacity-50 pointer-coarse:min-h-11';

/* ------------------------------ Zone & Frost ------------------------------ */

export function ZoneFrostPanel({ profile }: { profile?: GardenProfile }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [zone, setZone] = useState(profile?.hardiness_zone ?? '');
  const [spring, setSpring] = useState(profile?.last_spring_frost_mmdd ?? '');
  const [fall, setFall] = useState(profile?.first_fall_frost_mmdd ?? '');
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    setZone(profile?.hardiness_zone ?? '');
    setSpring(profile?.last_spring_frost_mmdd ?? '');
    setFall(profile?.first_fall_frost_mmdd ?? '');
  }, [profile?.hardiness_zone, profile?.last_spring_frost_mmdd, profile?.first_fall_frost_mmdd]);

  const detectZone = useMutation({
    mutationFn: () => api.gardenDetectZone(),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['garden-profile'] });
      setMessage(data.message || `Zone detected: ${data.zone}`);
      toast.success(`Zone detected: ${data.zone}`);
    },
    onError: () => toast.error('Zone detection is unavailable right now'),
  });

  const saveProfile = useMutation({
    mutationFn: () => api.gardenUpdateProfile({
      hardiness_zone: zone.trim() || null,
      zone_source: 'manual',
      last_spring_frost_mmdd: spring.trim() || null,
      first_fall_frost_mmdd: fall.trim() || null,
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['garden-profile'] });
      setEditing(false);
      toast.success('Garden climate saved');
    },
    onError: () => toast.error('Could not save the garden climate'),
  });

  return (
    <section className="bg-white/5 border border-white/10 rounded-2xl p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h3 className="text-sm font-semibold text-white uppercase tracking-wider">Household Climate</h3>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => detectZone.mutate()} disabled={detectZone.isPending} className={buttonClass}>
            <Compass size={14} className="inline mr-1" />
            {detectZone.isPending ? 'Detecting…' : 'Auto-detect'}
          </button>
          <button type="button" onClick={() => setEditing((open) => !open)} className={buttonClass}>
            {editing ? 'Close' : 'Edit'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <div className="bg-white/5 rounded-lg p-2">
          <div className="text-slate-400">USDA Zone</div>
          <div className="text-white font-bold">
            {profile?.hardiness_zone ?? '—'}
            {profile?.zone_source === 'auto' ? ' (auto)' : ''}
          </div>
        </div>
        <div className="bg-white/5 rounded-lg p-2">
          <div className="text-slate-400">Last Spring Frost</div>
          <div className="text-white font-bold">{profile?.last_spring_frost_mmdd ?? '—'}</div>
        </div>
        <div className="bg-white/5 rounded-lg p-2">
          <div className="text-slate-400">First Fall Frost</div>
          <div className="text-white font-bold">{profile?.first_fall_frost_mmdd ?? '—'}</div>
        </div>
        <div className="bg-white/5 rounded-lg p-2">
          <div className="text-slate-400">Family Size</div>
          <div className="text-white font-bold">{profile?.family_size ?? 7}</div>
        </div>
      </div>

      {message && <p className="mt-2 text-xs text-emerald-300">{message}</p>}

      {editing && (
        <form
          className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            saveProfile.mutate();
          }}
        >
          <label className={labelClass}>
            USDA zone (e.g. 6b)
            <input value={zone} onChange={(e) => setZone(e.target.value)} className={inputClass} />
          </label>
          <label className={labelClass}>
            Last spring frost (MM-DD)
            <input value={spring} onChange={(e) => setSpring(e.target.value)} className={inputClass} />
          </label>
          <label className={labelClass}>
            First fall frost (MM-DD)
            <input value={fall} onChange={(e) => setFall(e.target.value)} className={inputClass} />
          </label>
          <div className="sm:col-span-3 flex justify-end">
            <button
              type="submit"
              disabled={saveProfile.isPending}
              className="px-4 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 text-sm pointer-coarse:min-h-11"
            >
              {saveProfile.isPending ? 'Saving…' : 'Save climate'}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

/* ------------------------- Dual sizing + pantry --------------------------- */

type SizingCrop = {
  species_ref: string;
  common_name: string;
  plant_count: number;
  sqft: number;
  yield_per_plant_lbs?: number;
  conversion_source?: string;
};

type CalcCrop = {
  species_ref: string;
  common_name: string;
  raw_lbs_needed: number;
  plant_count: number;
  sqft: number;
  conversion_source?: string;
};

const PRESERVATION_METHODS = [
  ['water_bath_canning', 'Water-bath canning'],
  ['pressure_canning', 'Pressure canning'],
  ['freezing', 'Freezing'],
  ['dehydrating', 'Dehydrating'],
  ['fermenting', 'Fermenting'],
  ['cold_storage', 'Cold storage'],
  ['oil_herb_cubes', 'Herb cubes'],
] as const;

const UNITS = ['quarts', 'pints', 'lbs'] as const;

function PantryGoalCard({ goal }: { goal: PantryGoalOut }) {
  const queryClient = useQueryClient();
  const cropRefs = useMemo<string[]>(() => {
    try {
      const parsed = JSON.parse(goal.crop_refs_json || '[]');
      return Array.isArray(parsed) ? parsed.filter((entry): entry is string => typeof entry === 'string') : [];
    } catch {
      return [];
    }
  }, [goal.crop_refs_json]);

  const calc = useQuery({
    queryKey: ['pantry-goal-calc', goal.id, goal.target_qty, goal.unit, goal.method, cropRefs.join(',')],
    queryFn: () => api.gardenCalculatePantryGoal({
      item: goal.item,
      crop_refs: cropRefs.join(','),
      target_qty: goal.target_qty,
      unit: goal.unit,
      method: goal.method,
    }),
    staleTime: 60_000,
  });
  const result = calc.data as PantryGoalCalcResponse | undefined;

  const remove = useMutation({
    mutationFn: () => api.gardenDeletePantryGoal(goal.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['garden-pantry-goals'] });
      toast.success('Pantry goal removed');
    },
    onError: () => toast.error('Could not remove that goal'),
  });

  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-white">{goal.item}</div>
          <div className="text-xs text-slate-400">
            {goal.target_qty} {goal.unit} · {goal.method.replaceAll('_', ' ')} · {goal.season_year}
          </div>
        </div>
        <button type="button" onClick={() => remove.mutate()} className="text-red-400 hover:text-red-300 pointer-coarse:min-h-11 px-2" aria-label={`Remove ${goal.item}`}>
          <Trash2 size={14} />
        </button>
      </div>
      {result ? (
        <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
          <div className="bg-black/20 rounded-lg p-2">
            <div className="text-slate-400">Raw lbs</div>
            <div className="text-white font-semibold">{result.total_raw_lbs_needed}</div>
          </div>
          <div className="bg-black/20 rounded-lg p-2">
            <div className="text-slate-400">Plants</div>
            <div className="text-white font-semibold">{result.total_plant_count}</div>
          </div>
          <div className="bg-black/20 rounded-lg p-2">
            <div className="text-slate-400">Sq ft</div>
            <div className="text-white font-semibold">{result.total_sqft}</div>
          </div>
        </div>
      ) : (
        <p className="mt-2 text-xs text-slate-500">{calc.isLoading ? 'Calculating…' : 'No crop chosen yet.'}</p>
      )}
      {result?.crops?.length ? (
        <p className="mt-2 text-[11px] text-slate-400">
          {(result.crops as CalcCrop[]).map((crop) => `${crop.common_name}: ${crop.plant_count} plants`).join(' · ')}
        </p>
      ) : null}
    </div>
  );
}

export function DualSizingPanel({ familySize, species }: { familySize: number; species: GardenSpecies[] }) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'simple' | 'pantry'>('simple');
  const [sizingMode, setSizingMode] = useState<'fresh_only' | 'fresh_plus_preserving'>('fresh_only');
  const [selected, setSelected] = useState<string[]>([]);
  const [sizing, setSizing] = useState<PlannerSizingResponse | null>(null);

  const [item, setItem] = useState('');
  const [qty, setQty] = useState('10');
  const [unit, setUnit] = useState<string>('quarts');
  const [method, setMethod] = useState<string>('water_bath_canning');
  const [goalCrop, setGoalCrop] = useState('');

  const choices = useMemo(
    () => species.filter((entry) => ['vegetable', 'herb', 'fruit'].includes(entry.category)).slice(0, 60),
    [species],
  );

  useEffect(() => {
    if (choices.length && selected.length === 0) {
      setSelected(choices.slice(0, 3).map((entry) => entry.key));
    }
    if (choices.length && !goalCrop) setGoalCrop(choices[0].key);
  }, [choices, goalCrop, selected.length]);

  const calculate = useMutation({
    mutationFn: () => api.gardenPlannerSizing({
      mode: sizingMode,
      family_size: familySize,
      species_refs: selected.join(','),
    }),
    onSuccess: (data) => setSizing(data),
    onError: () => toast.error('Could not calculate the garden size'),
  });

  const goalsQuery = useQuery({
    queryKey: ['garden-pantry-goals'],
    queryFn: () => api.gardenListPantryGoals(),
    staleTime: 120_000,
  });
  const goals = goalsQuery.data?.goals ?? [];

  const addGoal = useMutation({
    mutationFn: () => api.gardenCreatePantryGoal({
      item: item.trim(),
      crop_refs_json: JSON.stringify(goalCrop ? [goalCrop] : []),
      target_qty: Number(qty) || 0,
      unit,
      method,
      season_year: new Date().getFullYear() + 1,
    }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['garden-pantry-goals'] });
      setItem('');
      toast.success('Pantry goal added');
    },
    onError: () => toast.error('Could not add that pantry goal'),
  });

  return (
    <section className="bg-white/5 border border-white/10 rounded-2xl p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h3 className="text-sm font-semibold text-white uppercase tracking-wider">Sizing</h3>
        <div className="inline-flex rounded-lg overflow-hidden border border-white/10">
          <button
            type="button"
            onClick={() => setMode('simple')}
            className={`px-3 py-1.5 text-xs pointer-coarse:min-h-11 ${mode === 'simple' ? 'bg-emerald-600 text-white' : 'bg-white/5 text-slate-300'}`}
          >
            Simple family sizing
          </button>
          <button
            type="button"
            onClick={() => setMode('pantry')}
            className={`px-3 py-1.5 text-xs pointer-coarse:min-h-11 ${mode === 'pantry' ? 'bg-emerald-600 text-white' : 'bg-white/5 text-slate-300'}`}
          >
            Pantry goals
          </button>
        </div>
      </div>

      {mode === 'simple' ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <label className={labelClass}>
              Family size
              <input value={familySize} readOnly className={inputClass} />
            </label>
            <label className={labelClass}>
              Mode
              <select value={sizingMode} onChange={(e) => setSizingMode(e.target.value as 'fresh_only' | 'fresh_plus_preserving')} className={inputClass}>
                <option value="fresh_only">Fresh only</option>
                <option value="fresh_plus_preserving">Fresh + preserving</option>
              </select>
            </label>
            <button type="button" onClick={() => calculate.mutate()} disabled={calculate.isPending || selected.length === 0} className={buttonClass}>
              {calculate.isPending ? 'Calculating…' : 'Calculate'}
            </button>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 max-h-48 overflow-y-auto pr-1">
            {choices.map((entry) => (
              <label key={entry.key} className="flex items-center gap-2 text-xs text-slate-300 bg-white/5 rounded-lg px-2 py-2">
                <input
                  type="checkbox"
                  checked={selected.includes(entry.key)}
                  onChange={(event) => {
                    setSelected((current) => event.target.checked
                      ? [...current, entry.key]
                      : current.filter((ref) => ref !== entry.key));
                  }}
                  className="accent-emerald-500"
                />
                {entry.common_name}
              </label>
            ))}
          </div>
          {sizing && (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-3 text-xs text-slate-300">
                <span className="bg-white/5 rounded-lg px-2 py-1">Total plants: <strong className="text-white">{sizing.total_plants}</strong></span>
                <span className="bg-white/5 rounded-lg px-2 py-1">Total sq ft: <strong className="text-white">{sizing.total_sqft}</strong></span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {(sizing.crops as SizingCrop[]).map((crop) => (
                  <div key={crop.species_ref} className="rounded-lg bg-white/5 border border-white/5 px-3 py-2 text-xs">
                    <div className="text-white font-medium">{crop.common_name}</div>
                    <div className="text-slate-400">{crop.plant_count} plants · {crop.sqft} sq ft</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <form
            className="grid grid-cols-1 sm:grid-cols-5 gap-2 items-end"
            onSubmit={(event) => {
              event.preventDefault();
              if (!item.trim()) return;
              addGoal.mutate();
            }}
          >
            <label className={labelClass}>
              Item
              <input value={item} onChange={(e) => setItem(e.target.value)} placeholder="Marinara sauce" className={inputClass} />
            </label>
            <label className={labelClass}>
              Quantity
              <input type="number" min="0" value={qty} onChange={(e) => setQty(e.target.value)} className={inputClass} />
            </label>
            <label className={labelClass}>
              Unit
              <select value={unit} onChange={(e) => setUnit(e.target.value)} className={inputClass}>
                {UNITS.map((entry) => <option key={entry} value={entry}>{entry}</option>)}
              </select>
            </label>
            <label className={labelClass}>
              Method
              <select value={method} onChange={(e) => setMethod(e.target.value)} className={inputClass}>
                {PRESERVATION_METHODS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className={labelClass}>
              Crop
              <select value={goalCrop} onChange={(e) => setGoalCrop(e.target.value)} className={inputClass}>
                {choices.map((entry) => <option key={entry.key} value={entry.key}>{entry.common_name}</option>)}
              </select>
            </label>
            <div className="sm:col-span-5 flex justify-end">
              <button type="submit" disabled={addGoal.isPending || !item.trim()} className="px-4 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 text-sm pointer-coarse:min-h-11">
                <Plus size={14} className="inline mr-1" /> {addGoal.isPending ? 'Adding…' : 'Add goal'}
              </button>
            </div>
          </form>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {goals.length === 0 ? (
              <p className="text-xs text-slate-500 italic">No pantry goals yet.</p>
            ) : (
              goals.map((goal) => <PantryGoalCard key={goal.id} goal={goal} />)
            )}
          </div>
        </div>
      )}
    </section>
  );
}

/* -------------------------------- Plans ---------------------------------- */

export function PlansPanel({
  plans, activePlanId, onSelect, onChanged,
}: {
  plans: GardenPlanOut[];
  activePlanId: string | null;
  onSelect: (planId: string) => void;
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [season, setSeason] = useState('spring_summer');
  const [year, setYear] = useState(String(new Date().getFullYear() + 1));
  const [familySize, setFamilySize] = useState('7');
  const [mode, setMode] = useState('fresh_only');

  const createPlan = useMutation({
    mutationFn: () => api.gardenCreatePlan({
      name: name.trim(),
      season,
      year: Number(year) || new Date().getFullYear() + 1,
      family_size: Number(familySize) || 7,
      mode,
      status: 'draft',
    }),
    onSuccess: (plan) => {
      setAdding(false);
      setName('');
      onChanged();
      onSelect(plan.id);
      toast.success('Plan created');
    },
    onError: () => toast.error('Could not create that plan'),
  });

  const updatePlan = useMutation({
    mutationFn: ({ planId, status }: { planId: string; status: string }) => api.gardenPatchPlan(planId, { status }),
    onSuccess: () => {
      onChanged();
      toast.success('Plan updated');
    },
    onError: () => toast.error('Could not update that plan'),
  });

  const deletePlan = useMutation({
    mutationFn: (planId: string) => api.gardenDeletePlan(planId),
    onSuccess: () => {
      onChanged();
      toast.success('Plan deleted');
    },
    onError: () => toast.error('Could not delete that plan'),
  });

  return (
    <section className="bg-white/5 border border-white/10 rounded-2xl p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h3 className="text-sm font-semibold text-white uppercase tracking-wider">Seasonal Plans</h3>
        <button type="button" onClick={() => setAdding((open) => !open)} className={buttonClass}>
          <Plus size={14} className="inline mr-1" /> New Plan
        </button>
      </div>

      {adding && (
        <form
          className="grid grid-cols-1 sm:grid-cols-5 gap-2 items-end mb-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!name.trim()) return;
            createPlan.mutate();
          }}
        >
          <label className={labelClass}>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Spring/Summer 2027" className={inputClass} />
          </label>
          <label className={labelClass}>
            Season
            <select value={season} onChange={(e) => setSeason(e.target.value)} className={inputClass}>
              <option value="spring_summer">Spring / Summer</option>
              <option value="fall">Fall</option>
              <option value="winter">Winter</option>
            </select>
          </label>
          <label className={labelClass}>
            Year
            <input type="number" value={year} onChange={(e) => setYear(e.target.value)} className={inputClass} />
          </label>
          <label className={labelClass}>
            Family size
            <input type="number" min="1" value={familySize} onChange={(e) => setFamilySize(e.target.value)} className={inputClass} />
          </label>
          <label className={labelClass}>
            Mode
            <select value={mode} onChange={(e) => setMode(e.target.value)} className={inputClass}>
              <option value="fresh_only">Fresh only</option>
              <option value="fresh_plus_preserving">Fresh + preserving</option>
              <option value="pantry_goals">Pantry goals</option>
            </select>
          </label>
          <div className="sm:col-span-5 flex justify-end">
            <button type="submit" disabled={createPlan.isPending || !name.trim()} className="px-4 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 text-sm pointer-coarse:min-h-11">
              {createPlan.isPending ? 'Creating…' : 'Create plan'}
            </button>
          </div>
        </form>
      )}

      {plans.length === 0 ? (
        <p className="text-xs text-slate-500 italic">No seasonal plans yet. Create one to start placing crops.</p>
      ) : (
        <div className="space-y-2">
          {plans.map((plan) => (
            <div
              key={plan.id}
              className={`flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-lg border ${
                activePlanId === plan.id ? 'bg-emerald-500/10 border-emerald-500/30' : 'bg-white/5 border-white/5'
              }`}
            >
              <button type="button" onClick={() => onSelect(plan.id)} className="text-left flex-1 min-w-40">
                <span className="block text-sm text-white font-medium">{plan.name}</span>
                <span className="block text-xs text-slate-400">
                  {plan.season.replaceAll('_', ' ')} {plan.year} · {plan.mode.replaceAll('_', ' ')} · {plan.family_size} family
                </span>
              </button>
              <div className="flex items-center gap-2">
                <span className={`text-xs px-1.5 py-0.5 rounded ${
                  plan.status === 'active' ? 'bg-emerald-500/20 text-emerald-300' : 'bg-slate-600/20 text-slate-300'
                }`}>
                  {plan.status}
                </span>
                {plan.status !== 'active' && (
                  <button type="button" onClick={() => updatePlan.mutate({ planId: plan.id, status: 'active' })} className={buttonClass}>
                    Activate
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => deletePlan.mutate(plan.id)}
                  className="text-red-400 hover:text-red-300 px-2 pointer-coarse:min-h-11"
                  aria-label={`Delete ${plan.name}`}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/* ------------------------------- Yard map --------------------------------- */

/** The outdoor places a bed can be (services/garden/models.py Location.kind). */
const BED_KINDS = [
  { value: 'raised_bed', label: 'Raised bed' },
  { value: 'in_ground', label: 'In ground' },
  { value: 'container', label: 'Container' },
  { value: 'trellis', label: 'Trellis' },
  { value: 'greenhouse', label: 'Greenhouse' },
  { value: 'orchard', label: 'Orchard' },
];

export function YardMapPanel({
  beds, onOpenBed,
}: {
  beds: GardenLocation[];
  onOpenBed: (bed: GardenLocation) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [width, setWidth] = useState('4');
  const [length, setLength] = useState('8');
  const [mapX, setMapX] = useState('1');
  const [mapY, setMapY] = useState('1');
  const [kind, setKind] = useState('raised_bed');

  const selected = beds.find((bed) => bed.id === selectedId) ?? null;

  const canvasWidth = Math.max(20, ...beds.map((bed) => (bed.map_x ?? 0) + (bed.width_ft ?? 4) + 3));
  const canvasHeight = Math.max(12, ...beds.map((bed) => (bed.map_y ?? 0) + (bed.length_ft ?? 8) + 3));

  const addBed = useMutation({
    mutationFn: () => api.gardenCreateLocation({
      name: name.trim(),
      kind,
      width_ft: Number(width) || 4,
      length_ft: Number(length) || 8,
      map_x: Number(mapX) || 0,
      map_y: Number(mapY) || 0,
      rotation_deg: 0,
    }),
    onSuccess: () => {
      setAdding(false);
      setName('');
      void queryClient.invalidateQueries({ queryKey: ['garden-locations'] });
      toast.success('Bed added to the yard');
    },
    onError: () => toast.error('Could not add that bed'),
  });

  const patchBed = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<GardenLocation> }) => api.gardenPatchLocation(id, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['garden-locations'] }),
    onError: () => toast.error('Could not move that bed'),
  });

  const move = (bed: GardenLocation, dx: number, dy: number) => {
    patchBed.mutate({ id: bed.id, body: { map_x: (bed.map_x ?? 0) + dx, map_y: (bed.map_y ?? 0) + dy } });
  };

  return (
    <section className="bg-white/5 border border-white/10 rounded-2xl p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h3 className="text-sm font-semibold text-white uppercase tracking-wider">Yard Map</h3>
        <button type="button" onClick={() => setAdding((open) => !open)} className={buttonClass}>
          <Plus size={14} className="inline mr-1" /> Add Bed
        </button>
      </div>

      {adding && (
        <form
          className="grid grid-cols-2 sm:grid-cols-6 gap-2 items-end mb-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!name.trim()) return;
            addBed.mutate();
          }}
        >
          <label className={labelClass}>
            Bed name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="North bed" className={inputClass} />
          </label>
          <label className={labelClass}>
            Kind
            <select value={kind} onChange={(e) => setKind(e.target.value)} className={inputClass}>
              {BED_KINDS.map((bedKind) => (
                <option key={bedKind.value} value={bedKind.value}>{bedKind.label}</option>
              ))}
            </select>
          </label>
          <label className={labelClass}>
            Width (ft)
            <input type="number" min="1" value={width} onChange={(e) => setWidth(e.target.value)} className={inputClass} />
          </label>
          <label className={labelClass}>
            Length (ft)
            <input type="number" min="1" value={length} onChange={(e) => setLength(e.target.value)} className={inputClass} />
          </label>
          <label className={labelClass}>
            X (ft)
            <input type="number" value={mapX} onChange={(e) => setMapX(e.target.value)} className={inputClass} />
          </label>
          <label className={labelClass}>
            Y (ft)
            <input type="number" value={mapY} onChange={(e) => setMapY(e.target.value)} className={inputClass} />
          </label>
          <div className="col-span-2 sm:col-span-6 flex justify-end">
            <button type="submit" disabled={addBed.isPending || !name.trim()} className="px-4 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 text-sm pointer-coarse:min-h-11">
              {addBed.isPending ? 'Adding…' : 'Add bed'}
            </button>
          </div>
        </form>
      )}

      {beds.length === 0 ? (
        <p className="text-xs text-slate-500 italic">No outdoor beds yet. Add one to draw the yard.</p>
      ) : (
        <div className="rounded-xl bg-emerald-950/30 border border-emerald-900/40 p-2">
          <svg viewBox={`0 0 ${canvasWidth} ${canvasHeight}`} className="w-full h-auto" role="img" aria-label="Yard map with outdoor beds">
            <text x={canvasWidth / 2} y={1.2} textAnchor="middle" fill="#94a3b8" fontSize="1.2">N</text>
            <text x={canvasWidth / 2} y={canvasHeight - 0.4} textAnchor="middle" fill="#94a3b8" fontSize="1.2">S</text>
            <text x={0.3} y={canvasHeight / 2} fill="#94a3b8" fontSize="1.2">W</text>
            <text x={canvasWidth - 1} y={canvasHeight / 2} fill="#94a3b8" fontSize="1.2">E</text>
            {beds.map((bed) => {
              const x = bed.map_x ?? 0;
              const y = bed.map_y ?? 0;
              const w = bed.width_ft ?? 4;
              const h = bed.length_ft ?? 8;
              const rotation = bed.rotation_deg ?? 0;
              return (
                <g
                  key={bed.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`Open ${bed.name} bed grid`}
                  onClick={() => { setSelectedId(bed.id); onOpenBed(bed); }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setSelectedId(bed.id);
                      onOpenBed(bed);
                    }
                  }}
                  className="cursor-pointer"
                >
                  <rect
                    x={x}
                    y={y}
                    width={w}
                    height={h}
                    rx={0.4}
                    fill={selectedId === bed.id ? 'rgba(16,185,129,0.35)' : 'rgba(16,185,129,0.18)'}
                    stroke={selectedId === bed.id ? '#34d399' : '#059669'}
                    strokeWidth={0.15}
                    transform={`rotate(${rotation} ${x + w / 2} ${y + h / 2})`}
                  />
                  <text
                    x={x + w / 2}
                    y={y + h / 2}
                    textAnchor="middle"
                    fill="#d1fae5"
                    fontSize="0.9"
                    transform={`rotate(${rotation} ${x + w / 2} ${y + h / 2})`}
                  >
                    {bed.name}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      )}

      {selected && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-white/5 border border-white/10 p-2">
          <span className="text-xs text-slate-300 flex items-center gap-1"><Leaf size={12} /> {selected.name}</span>
          <button type="button" onClick={() => move(selected, 0, -1)} className={buttonClass} aria-label="Move bed north">↑</button>
          <button type="button" onClick={() => move(selected, 0, 1)} className={buttonClass} aria-label="Move bed south">↓</button>
          <button type="button" onClick={() => move(selected, -1, 0)} className={buttonClass} aria-label="Move bed west">←</button>
          <button type="button" onClick={() => move(selected, 1, 0)} className={buttonClass} aria-label="Move bed east">→</button>
          <button
            type="button"
            onClick={() => patchBed.mutate({ id: selected.id, body: { rotation_deg: ((selected.rotation_deg ?? 0) + 90) % 360 } })}
            className={buttonClass}
            aria-label="Rotate bed 90 degrees"
          >
            <RotateCw size={13} className="inline mr-1" /> Rotate
          </button>
          <button type="button" onClick={() => onOpenBed(selected)} className={buttonClass}>
            <Sprout size={13} className="inline mr-1" /> Open grid
          </button>
        </div>
      )}
    </section>
  );
}

/* --------------------------- Bed grid editor ----------------------------- */

const CHECK_STYLES: Record<string, string> = {
  companion: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-200',
  antagonist: 'bg-red-500/10 border-red-500/30 text-red-200',
  rotation: 'bg-amber-500/10 border-amber-500/30 text-amber-200',
  shading: 'bg-orange-500/10 border-orange-500/30 text-orange-200',
};

export function BedGridEditor({
  planId, bed, species, onClose, canEdit = false,
}: {
  planId: string;
  bed: GardenLocation;
  species: GardenSpecies[];
  onClose: () => void;
  /** Parents and admins may bind this bed's sensors, valve, light and tag. */
  canEdit?: boolean;
}) {
  const queryClient = useQueryClient();
  const width = Math.max(1, Math.round(bed.width_ft ?? 4));
  const height = Math.max(1, Math.round(bed.length_ft ?? 8));
  const [assigned, setAssigned] = useState<Record<string, string>>({});
  const [selectedCrop, setSelectedCrop] = useState('');
  const [search, setSearch] = useState('');

  const cellsQuery = useQuery({
    queryKey: ['garden-plan-cells', planId, bed.id],
    queryFn: () => api.gardenListPlanCells(planId, bed.id),
  });

  useEffect(() => {
    const next: Record<string, string> = {};
    for (const cell of cellsQuery.data?.cells ?? []) {
      if (cell.species_ref) next[`${cell.col},${cell.row}`] = cell.species_ref;
    }
    setAssigned(next);
  }, [cellsQuery.data]);

  const checksQuery = useQuery({
    queryKey: ['garden-plan-checks', planId],
    queryFn: () => api.gardenPlanChecks(planId),
  });
  const checks = (checksQuery.data?.checks ?? []).filter(
    (check: { bed_id?: string }) => check.bed_id === bed.id,
  );

  const speciesByKey = useMemo(() => {
    const map = new Map<string, GardenSpecies>();
    for (const entry of species) map.set(entry.key, entry);
    return map;
  }, [species]);

  const filteredSpecies = useMemo(() => {
    const query = search.trim().toLowerCase();
    return species
      .filter((entry) => ['vegetable', 'herb', 'fruit'].includes(entry.category))
      .filter((entry) => !query
        || entry.common_name.toLowerCase().includes(query)
        || entry.key.toLowerCase().includes(query))
      .slice(0, 40);
  }, [search, species]);

  const saveCells = useMutation({
    mutationFn: () => {
      const payload: PlanCellIn[] = Object.entries(assigned).map(([key, ref]) => {
        const [col, row] = key.split(',').map(Number);
        const found = speciesByKey.get(ref);
        return {
          plan_id: planId,
          location_id: bed.id,
          col,
          row,
          species_ref: ref,
          crop_family: (found?.crop_family as string | undefined) ?? null,
          quantity: 1,
        };
      });
      return api.gardenUpsertPlanCells(planId, bed.id, payload);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['garden-plan-cells', planId, bed.id] });
      void queryClient.invalidateQueries({ queryKey: ['garden-plan-checks', planId] });
      void queryClient.invalidateQueries({ queryKey: ['garden-plan-timeline', planId] });
      toast.success('Bed grid saved');
    },
    onError: () => toast.error('Could not save the bed grid'),
  });

  const autoArrange = useMutation({
    // Arrange what is on the grid now, not only what was last saved: a person
    // taps Auto-arrange on a bed they have just filled in.
    mutationFn: () => api.gardenAutoArrange(planId, bed.id, {
      species_refs: Object.values(assigned).filter(Boolean),
      apply: true,
    }),
    onSuccess: (data) => {
      const next: Record<string, string> = {};
      for (const placement of data.placements ?? []) {
        if (placement.col >= 0 && placement.species_ref) {
          next[`${placement.col},${placement.row}`] = placement.species_ref;
        }
      }
      setAssigned(next);
      void queryClient.invalidateQueries({ queryKey: ['garden-plan-cells', planId, bed.id] });
      void queryClient.invalidateQueries({ queryKey: ['garden-plan-checks', planId] });
      toast.success('Bed auto-arranged');
    },
    onError: () => toast.error('Could not auto-arrange this bed'),
  });

  return (
    <section className="bg-white/5 border border-emerald-500/20 rounded-2xl p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div>
          <h3 className="text-sm font-semibold text-white uppercase tracking-wider">{bed.name} · Square-foot grid</h3>
          <p className="text-xs text-slate-400">{width}' × {height}' · tap a cell to plant the selected crop</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => autoArrange.mutate()} disabled={autoArrange.isPending} className={buttonClass}>
            <Sprout size={13} className="inline mr-1" /> {autoArrange.isPending ? 'Arranging…' : 'Auto-arrange'}
          </button>
          <button type="button" onClick={() => saveCells.mutate()} disabled={saveCells.isPending} className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 text-xs disabled:opacity-50 pointer-coarse:min-h-11">
            <Check size={13} className="inline mr-1" /> {saveCells.isPending ? 'Saving…' : 'Save Grid'}
          </button>
          <button type="button" onClick={onClose} className={`${buttonClass} pointer-coarse:min-w-11 inline-flex items-center justify-center`} aria-label="Close bed grid">
            <X size={14} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-4">
        <div className="min-w-0">
          <div className="max-w-full overflow-x-auto">
            <div className="inline-block rounded-xl bg-emerald-950/30 border border-emerald-900/40 p-2">
              <div className="text-[10px] text-slate-400 text-center mb-1">N</div>
              <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${width}, minmax(0, 1fr))` }}>
                {Array.from({ length: height }, (_, index) => height - 1 - index).flatMap((row) =>
                  Array.from({ length: width }, (_, col) => {
                    const key = `${col},${row}`;
                    const ref = assigned[key];
                    const crop = ref ? speciesByKey.get(ref) : undefined;
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => {
                          setAssigned((current) => {
                            const next = { ...current };
                            if (!selectedCrop || next[key] === selectedCrop) delete next[key];
                            else next[key] = selectedCrop;
                            return next;
                          });
                        }}
                        aria-label={`Cell ${col}, ${row}${crop ? `: ${crop.common_name}` : ''}`}
                        title={crop?.common_name ?? `Cell ${col}, ${row}`}
                        className={`h-11 w-11 rounded-md border text-[9px] leading-tight overflow-hidden px-0.5 ${
                          crop
                            ? 'bg-emerald-500/30 border-emerald-400/50 text-emerald-50'
                            : 'bg-white/5 border-white/10 text-slate-500 hover:bg-white/10'
                        }`}
                      >
                        {crop ? crop.common_name.slice(0, 9) : '+'}
                      </button>
                    );
                  }),
                )}
              </div>
              <div className="text-[10px] text-slate-400 text-center mt-1">S</div>
            </div>
          </div>

          <div className="mt-3 space-y-1">
            {checks.length === 0 ? (
              <p className="text-xs text-slate-500">No companion, antagonist, rotation, or shading alerts in this bed.</p>
            ) : (
              checks.map((check: { cell_id?: string; kind?: string; message?: string }, index) => (
                <div
                  key={`${check.cell_id}-${check.kind}-${index}`}
                  className={`text-xs rounded-lg border px-2 py-1.5 ${CHECK_STYLES[check.kind ?? ''] ?? 'bg-white/5 border-white/10 text-slate-200'}`}
                >
                  {check.kind !== 'companion' && <AlertTriangle size={12} className="inline mr-1" />}
                  {check.message}
                </div>
              ))
            )}
          </div>
        </div>

        <div>
          <label className={labelClass}>
            Search crops
            <span className="relative block">
              <Search size={13} className="absolute left-2 top-2.5 text-slate-500" />
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="tomato, basil…" className={`${inputClass} pl-7`} />
            </span>
          </label>
          <div className="mt-2 max-h-56 overflow-y-auto space-y-1 pr-1">
            {filteredSpecies.map((entry) => (
              <button
                key={entry.key}
                type="button"
                onClick={() => setSelectedCrop(entry.key)}
                className={`w-full text-left px-2.5 py-2 min-h-11 rounded-lg border text-xs ${
                  selectedCrop === entry.key
                    ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-100'
                    : 'bg-white/5 border-white/5 text-slate-300 hover:bg-white/10'
                }`}
              >
                {entry.common_name}
                <span className="block text-[10px] text-slate-500">{entry.category}</span>
              </button>
            ))}
          </div>
          {selectedCrop && (
            <button
              type="button"
              onClick={() => setSelectedCrop('')}
              className="mt-2 text-xs text-slate-400 hover:text-slate-200 underline pointer-coarse:min-h-11"
            >
              Clear selected crop
            </button>
          )}
        </div>
      </div>
      {/* A bed is where the valve and the soil probe actually are. */}
      <div className="mt-4">
        <HomeHubBindingPanel targetKind="location" targetId={bed.id} canEdit={canEdit} />
      </div>
    </section>
  );
}

/* ------------------------------- Timeline -------------------------------- */

type TimelineEntry = {
  cell_id?: string;
  common_name?: string;
  species_ref?: string;
  milestones?: Array<{ stage?: string; mmdd?: string; week_offset?: number }>;
};

export function TimelinePanel({ planId, planYear }: { planId: string; planYear: number }) {
  const [busy, setBusy] = useState(false);
  const timelineQuery = useQuery({
    queryKey: ['garden-plan-timeline', planId],
    queryFn: () => api.gardenPlanTimeline(planId),
    staleTime: 60_000,
  });
  const timeline = (timelineQuery.data?.timeline ?? []) as TimelineEntry[];

  const entries = useMemo(() => {
    const flat: Array<{ date: string; name: string; stage: string }> = [];
    for (const item of timeline) {
      for (const milestone of item.milestones ?? []) {
        if (milestone.mmdd && milestone.stage) {
          flat.push({ date: milestone.mmdd, name: item.common_name || item.species_ref || 'Crop', stage: milestone.stage });
        }
      }
    }
    return flat.sort((a, b) => a.date.localeCompare(b.date));
  }, [timeline]);

  const addToCalendar = async () => {
    setBusy(true);
    try {
      const results = await Promise.allSettled(entries.map((entry) => api.addCalendarEvent({
        summary: `Garden: ${entry.name} — ${entry.stage}`,
        // `start_time` is an ISO instant: a date alone made Calendar guess.
        start_time: `${planYear}-${entry.date} 09:00`,
      })));
      // A resolved promise still carries the service's own verdict (SUCCESS/FAILURE).
      const failed = results.filter(
        (result) => result.status === 'rejected' || result.value?.status !== 'SUCCESS',
      ).length;
      if (failed) toast.error('Some milestones could not reach Calendar; they stay on this plan.');
      else toast.success('Garden milestones added to Calendar');
    } catch {
      toast.error('Calendar is not configured; milestones stay on this plan.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="bg-white/5 border border-white/10 rounded-2xl p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h3 className="text-sm font-semibold text-white uppercase tracking-wider">Seasonal Timeline</h3>
        <button type="button" onClick={() => void addToCalendar()} disabled={busy || entries.length === 0} className={buttonClass}>
          <CalendarPlus size={14} className="inline mr-1" /> {busy ? 'Adding…' : 'Add to Calendar'}
        </button>
      </div>
      {entries.length === 0 ? (
        <p className="text-xs text-slate-500 italic">No dated milestones yet. Add crops to a bed and set the climate.</p>
      ) : (
        <ol className="relative border-l border-white/10 ml-2 space-y-2">
          {entries.map((entry, index) => (
            <li key={`${entry.date}-${entry.name}-${entry.stage}-${index}`} className="ml-3">
              <span className="absolute -left-1 mt-1.5 h-2 w-2 rounded-full bg-emerald-400" />
              <div className="text-xs text-slate-400">{entry.date}</div>
              <div className="text-sm text-white">{entry.name} <span className="text-slate-400">· {entry.stage}</span></div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
