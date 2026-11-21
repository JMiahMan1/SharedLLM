import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import {
  AlertTriangle, Pencil, ShieldCheck, ShoppingCart, Sprout, Trash2, X,
} from 'lucide-react';
import { api } from '../../services/api';
import type {
  HarvestForecastItem, PantryBatch, PantryGoalProgress, PreservationCombo, PreservationCrop,
} from '../../types/api';

const cardClass = 'rounded-xl border border-white/10 bg-white/5 p-3';
const inputClass =
  'w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-white placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-emerald-500 text-sm pointer-coarse:min-h-11';
const labelClass = 'flex flex-col gap-1 text-[10px] text-slate-400';
const buttonClass =
  'inline-flex items-center justify-center gap-1.5 px-3 py-1.5 min-h-11 rounded-lg bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10 text-xs transition-colors disabled:opacity-50';

/** Units and methods the pantry understands (the catalog is the source of truth). */
const PANTRY_UNITS = ['quarts', 'pints', 'half_pints', 'lbs', 'bags', 'jars', 'bunches'];
const PANTRY_METHODS = [
  'water_bath_canning', 'pressure_canning', 'freezing', 'dehydrating',
  'fermenting', 'cold_storage', 'oil_herb_cubes',
];

function methodName(method: string) {
  return method.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function dayLabel(days: number) {
  if (days < 0) return `${Math.abs(days)} days overdue`;
  if (days === 0) return 'Ready today';
  if (days === 1) return 'Ready tomorrow';
  return `Ready in ${days} days`;
}

function shortDate(iso: string | null) {
  if (!iso) return '';
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/* ------------------------------ Coming Ready Soon ------------------------------ */

export function HarvestForecastPanel({
  items, weatherUsed, onOpenRecipes,
}: {
  items: HarvestForecastItem[];
  weatherUsed: boolean;
  onOpenRecipes: (crop: string) => void;
}) {
  return (
    <section className={cardClass} aria-labelledby="pantry-forecast-heading">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <h3 id="pantry-forecast-heading" className="text-sm font-semibold text-slate-200 inline-flex items-center gap-2">
          <Sprout size={16} /> Coming Ready Soon
        </h3>
        <span className="text-[10px] text-slate-400">
          {weatherUsed ? 'Tuned with growing degree days' : 'From days-to-maturity (no weather data)'}
        </span>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-slate-400">
          Nothing ripening in the next few weeks. Add a planting date to an outdoor crop to see a forecast.
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.plant_id} className="rounded-lg border border-white/10 bg-slate-950/40 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-white">{item.nickname || item.common_name}</span>
                {item.nickname && <span className="text-[11px] text-slate-400">{item.common_name}</span>}
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300">
                  {dayLabel(item.days_until_ready)}
                </span>
                {item.gdd_adjusted && (
                  <span
                    className="text-[10px] px-2 py-0.5 rounded-full bg-sky-500/15 text-sky-300"
                    title={`Growing degree days above ${item.gdd_base_f}°F (${item.gdd_shift_days > 0 ? '+' : ''}${item.gdd_shift_days} days vs. plain maturity)`}
                  >
                    GDD
                  </span>
                )}
              </div>
              <p className="text-[11px] text-slate-400 mt-1">
                Est. {shortDate(item.estimated_ready_date)} · window to {shortDate(item.harvest_window_end)}
                {item.blanching_minutes ? ` · blanch ${item.blanching_minutes} min to freeze` : ''}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {item.preservation_methods.map((method) => (
                  <span key={method} className="text-[10px] px-2 py-0.5 rounded-full bg-white/5 text-slate-300">
                    {methodName(method)}
                  </span>
                ))}
                <button type="button" onClick={() => onOpenRecipes(item.culinary_aliases[0] || item.common_name)} className={buttonClass}>
                  Recipes
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ------------------------------ Preservation advisor ------------------------------ */

function SafetyCallout({ safety }: { safety: PreservationCrop['safety'] }) {
  const pressure = safety.level === 'pressure_canner_required';
  const Icon = pressure ? AlertTriangle : safety.level === 'water_bath_safe' ? ShieldCheck : AlertTriangle;
  return (
    <div
      className={`mt-2 rounded-lg border p-2 ${
        pressure
          ? 'border-amber-500/40 bg-amber-500/10 text-amber-200'
          : safety.level === 'water_bath_safe'
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
            : 'border-white/15 bg-white/5 text-slate-300'
      }`}
    >
      <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
        <Icon size={13} /> {safety.title}
      </p>
      <p className="text-[11px] mt-1 leading-relaxed">{safety.message}</p>
      <p className="text-[10px] mt-1 opacity-80">{safety.source}</p>
    </div>
  );
}

export function PreservationCropCard({ crop }: { crop: PreservationCrop }) {
  return (
    <li className="rounded-lg border border-white/10 bg-slate-950/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-white">{crop.common_name}</span>
        <span className="text-[10px] text-slate-400">
          {crop.plant_count} plant{crop.plant_count === 1 ? '' : 's'}
        </span>
      </div>
      <ul className="mt-2 space-y-1">
        {crop.methods.map((method) => (
          <li key={method.method} className="text-[11px] text-slate-300">
            <span className="text-white">{method.label}</span>
            {method.blanching_minutes ? ` · blanch ${method.blanching_minutes} min` : ''}
            {method.storage ? ` · ${method.storage}` : ''}
            {method.requires_acidification ? ' · acidify (tested recipe)' : ''}
          </li>
        ))}
      </ul>
      {crop.herb_preservation_tips && (
        <p className="text-[11px] text-slate-400 mt-1">{crop.herb_preservation_tips}</p>
      )}
      <SafetyCallout safety={crop.safety} />
    </li>
  );
}

export function ComboCard({ combo }: { combo: PreservationCombo }) {
  return (
    <li className="rounded-lg border border-white/10 bg-slate-950/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-white">{combo.label}</span>
        <span className={`text-[10px] px-2 py-0.5 rounded-full ${combo.ready ? 'bg-emerald-500/15 text-emerald-300' : 'bg-white/5 text-slate-300'}`}>
          {combo.ready ? 'You have everything' : `${combo.matched_count} of ${combo.required_count} main crops`}
        </span>
        <span className="text-[10px] text-slate-400">{combo.method_label}</span>
      </div>
      <p className="text-[11px] text-slate-400 mt-1">
        {combo.matched_crops.join(' + ')}
        {combo.missing_crops.length > 0 ? ` · missing ${combo.missing_crops.join(', ')}` : ''}
      </p>
      {combo.note && <p className="text-[11px] text-slate-400 mt-1">{combo.note}</p>}
      {combo.ready && <SafetyCallout safety={combo.safety} />}
    </li>
  );
}

/* ------------------------------ Put-up pantry ------------------------------ */

export function GoalProgressBar({ goal }: { goal: PantryGoalProgress }) {
  return (
    <li className="rounded-lg border border-white/10 bg-slate-950/40 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm text-white">{goal.item}</span>
        <span className="text-[11px] text-slate-300">
          {goal.qty_put_up} / {goal.target_qty} {goal.unit} put up ({goal.percent_complete}%)
        </span>
      </div>
      <div
        className="mt-2 h-2 w-full rounded-full bg-white/10 overflow-hidden"
        role="progressbar"
        aria-valuenow={goal.percent_complete}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${goal.item} progress`}
      >
        <div className="h-full rounded-full bg-emerald-500" style={{ width: `${goal.percent_complete}%` }} />
      </div>
      <p className="text-[10px] text-slate-400 mt-1">
        {goal.qty_remaining} {goal.unit} left · {goal.batch_count} batch{goal.batch_count === 1 ? '' : 'es'}
        {goal.unit_mismatch ? ` · ignoring batches in ${goal.mismatched_units.join(', ')}` : ''}
      </p>
    </li>
  );
}

export function BatchCard({
  batch, canEdit, busy, onUse, onEdit, onDelete,
}: {
  batch: PantryBatch;
  canEdit: boolean;
  busy: boolean;
  onUse: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <li className={`rounded-lg border p-3 ${batch.use_first ? 'border-amber-500/40 bg-amber-500/5' : 'border-white/10 bg-slate-950/40'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-white">{batch.label}</span>
        {batch.use_first && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-200">
            {batch.past_use_by ? 'Past use-by' : 'Use first'}
          </span>
        )}
      </div>
      <p className="text-[11px] text-slate-400 mt-1">
        {batch.qty_remaining} of {batch.qty_initial} {batch.unit} left · {batch.method_label || methodName(batch.method)}
        {batch.storage_location ? ` · ${batch.storage_location}` : ''}
        {batch.use_by_date ? ` · use by ${shortDate(batch.use_by_date)}` : ''}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" onClick={onUse} disabled={busy} className={`${buttonClass} !bg-amber-500/20 !text-amber-200`}>
          −1 Used
        </button>
        {canEdit && (
          <>
            <button type="button" onClick={onEdit} className={buttonClass} aria-label={`Edit ${batch.label}`}>
              <Pencil size={13} /> Edit
            </button>
            <button type="button" onClick={onDelete} className={`${buttonClass} !text-rose-300`} aria-label={`Delete ${batch.label}`}>
              <Trash2 size={13} /> Delete
            </button>
          </>
        )}
      </div>
    </li>
  );
}

/* ------------------------------ Log / edit a batch ------------------------------ */

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function BatchModal({
  batch, goals, onClose,
}: {
  batch: PantryBatch | null;
  goals: { id: string; item: string }[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState(batch?.label ?? '');
  const [goalId, setGoalId] = useState(batch?.goal_id ?? '');
  const [method, setMethod] = useState(batch?.method ?? PANTRY_METHODS[0]);
  const [qty, setQty] = useState(batch ? String(batch.qty_initial) : '12');
  const [unit, setUnit] = useState(batch?.unit ?? 'quarts');
  const [storage, setStorage] = useState(batch?.storage_location ?? '');
  const [batchDate, setBatchDate] = useState(batch?.batch_date ?? todayIso());
  const [useBy, setUseBy] = useState(batch?.use_by_date ?? '');
  const [notes, setNotes] = useState(batch?.notes ?? '');

  const save = useMutation({
    mutationFn: () => {
      const body = {
        label: label.trim(),
        goal_id: goalId || null,
        method,
        qty_initial: Number(qty) || 0,
        unit,
        storage_location: storage.trim() || null,
        batch_date: batchDate || null,
        use_by_date: useBy || null,
        notes: notes.trim() || null,
      };
      return batch
        ? api.gardenPatchPantryBatch(batch.id, body)
        : api.gardenCreatePantryBatch(body);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['garden-pantry'] });
      toast.success(batch ? 'Batch updated' : 'Batch put up');
      onClose();
    },
    onError: () => toast.error('Could not save the batch'),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={batch ? 'Edit pantry batch' : 'Log preserved batch'}
        className="bg-slate-900 border border-white/20 rounded-2xl p-6 w-full max-w-md max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold text-white">{batch ? 'Edit Batch' : 'Log Preserved Batch'}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="min-h-11 min-w-11 inline-flex items-center justify-center rounded-lg text-slate-400 hover:text-white">
            <X size={18} />
          </button>
        </div>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (!label.trim()) {
              toast.error('Give the batch a name');
              return;
            }
            save.mutate();
          }}
        >
          <label className={labelClass} htmlFor="pantry-label">
            What did you put up?
            <input id="pantry-label" className={inputClass} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Marinara sauce" />
          </label>
          <label className={labelClass} htmlFor="pantry-goal">
            Counts towards a goal (optional)
            <select id="pantry-goal" className={inputClass} value={goalId} onChange={(e) => setGoalId(e.target.value)}>
              <option value="">No goal</option>
              {goals.map((goal) => (
                <option key={goal.id} value={goal.id}>{goal.item}</option>
              ))}
            </select>
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className={labelClass} htmlFor="pantry-method">
              Method
              <select id="pantry-method" className={inputClass} value={method} onChange={(e) => setMethod(e.target.value)}>
                {PANTRY_METHODS.map((m) => (
                  <option key={m} value={m}>{methodName(m)}</option>
                ))}
              </select>
            </label>
            <label className={labelClass} htmlFor="pantry-storage">
              Stored where
              <input id="pantry-storage" className={inputClass} value={storage} onChange={(e) => setStorage(e.target.value)} placeholder="Basement shelf" />
            </label>
            <label className={labelClass} htmlFor="pantry-qty">
              Quantity
              <input id="pantry-qty" className={inputClass} type="number" min="0" step="0.5" value={qty} onChange={(e) => setQty(e.target.value)} />
            </label>
            <label className={labelClass} htmlFor="pantry-unit">
              Unit
              <select id="pantry-unit" className={inputClass} value={unit} onChange={(e) => setUnit(e.target.value)}>
                {PANTRY_UNITS.map((u) => (
                  <option key={u} value={u}>{u.replace(/_/g, ' ')}</option>
                ))}
              </select>
            </label>
            <label className={labelClass} htmlFor="pantry-batch-date">
              Put up on
              <input id="pantry-batch-date" className={inputClass} type="date" value={batchDate} onChange={(e) => setBatchDate(e.target.value)} />
            </label>
            <label className={labelClass} htmlFor="pantry-use-by">
              Use by (blank = from method)
              <input id="pantry-use-by" className={inputClass} type="date" value={useBy} onChange={(e) => setUseBy(e.target.value)} />
            </label>
          </div>
          <label className={labelClass} htmlFor="pantry-notes">
            Notes
            <textarea id="pantry-notes" className={inputClass} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
          <div className="flex flex-wrap gap-3 pt-2">
            <button type="button" onClick={onClose} className={`${buttonClass} flex-1`}>Cancel</button>
            <button type="submit" disabled={save.isPending} className={`${buttonClass} flex-1 !bg-emerald-600 !text-white`}>
              {save.isPending ? 'Saving…' : batch ? 'Save changes' : 'Add batch'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ------------------------------ The Pantry tab ------------------------------ */

export function PantryTab({
  canEdit, onAddSupplies, onOpenRecipes,
}: {
  canEdit: boolean;
  onAddSupplies: (items: string[]) => void;
  onOpenRecipes: () => void;
}) {
  const queryClient = useQueryClient();
  const forecastQuery = useQuery({
    queryKey: ['garden-harvest-forecast'],
    queryFn: () => api.gardenHarvestForecast({ days: 28 }),
    staleTime: 120_000,
  });
  const adviceQuery = useQuery({
    queryKey: ['garden-preservation-advice'],
    queryFn: () => api.gardenPreservationAdvice({ days: 28 }),
    staleTime: 120_000,
  });
  const pantryQuery = useQuery({
    queryKey: ['garden-pantry'],
    queryFn: () => api.gardenListPantryBatches(),
    staleTime: 60_000,
  });
  const goalsQuery = useQuery({
    queryKey: ['garden-pantry-goals'],
    queryFn: () => api.gardenListPantryGoals(),
    staleTime: 300_000,
    enabled: canEdit,
  });

  const [logOpen, setLogOpen] = useState(false);
  const [editing, setEditing] = useState<PantryBatch | null>(null);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['garden-pantry'] });
  };

  const useBatch = useMutation({
    mutationFn: (batch: PantryBatch) => api.gardenUsePantryBatch(batch.id, 1),
    onSuccess: (batch) => {
      refresh();
      toast.success(batch.qty_remaining > 0 ? `${batch.qty_remaining} ${batch.unit} left` : 'That was the last one');
    },
    onError: () => toast.error('Could not update the pantry'),
  });

  const deleteBatch = useMutation({
    mutationFn: (batch: PantryBatch) => api.gardenDeletePantryBatch(batch.id),
    onSuccess: () => {
      refresh();
      toast.success('Batch removed');
    },
    onError: () => toast.error('Could not remove the batch'),
  });

  const forecast = forecastQuery.data;
  const advice = adviceQuery.data;
  const pantry = pantryQuery.data;
  const useFirst = useMemo(
    () => (pantry?.batches ?? []).filter((batch) => batch.use_first),
    [pantry?.batches],
  );

  return (
    <div className="space-y-4">
      <HarvestForecastPanel
        items={forecast?.items ?? []}
        weatherUsed={Boolean(forecast?.weather_used)}
        onOpenRecipes={() => onOpenRecipes()}
      />

      <section className={cardClass} aria-labelledby="pantry-advice-heading">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <h3 id="pantry-advice-heading" className="text-sm font-semibold text-slate-200">
            Preservation Advisor
          </h3>
          {(advice?.prep_checklist.count ?? 0) > 0 && (
            <button
              type="button"
              onClick={() => onAddSupplies(advice?.prep_checklist.items ?? [])}
              className={`${buttonClass} !bg-amber-500/20 !text-amber-200`}
            >
              <ShoppingCart size={13} /> Prep supplies ({advice?.prep_checklist.count})
            </button>
          )}
        </div>
        {(advice?.crops.length ?? 0) === 0 ? (
          <p className="text-xs text-slate-400">
            Plant a vegetable, fruit or herb to see preservation methods and USDA safety notes.
          </p>
        ) : (
          <ul className="space-y-2">
            {advice?.crops.map((crop) => <PreservationCropCard key={crop.species_ref} crop={crop} />)}
          </ul>
        )}
        {(advice?.combos.length ?? 0) > 0 && (
          <div className="mt-3">
            <h4 className="text-[11px] font-semibold text-slate-300 mb-2">Combo ideas from what you are growing</h4>
            <ul className="space-y-2">
              {advice?.combos.map((combo) => <ComboCard key={combo.key} combo={combo} />)}
            </ul>
          </div>
        )}
      </section>

      <section className={cardClass} aria-labelledby="pantry-batches-heading">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <h3 id="pantry-batches-heading" className="text-sm font-semibold text-slate-200">Put-Up Pantry</h3>
          {canEdit && (
            <button type="button" onClick={() => setLogOpen(true)} className={`${buttonClass} !bg-emerald-600 !text-white`}>
              Log Batch
            </button>
          )}
        </div>

        {(pantry?.goal_progress.length ?? 0) > 0 && (
          <ul className="space-y-2 mb-3">
            {pantry?.goal_progress.map((goal) => <GoalProgressBar key={goal.goal_id} goal={goal} />)}
          </ul>
        )}

        {useFirst.length > 0 && (
          <p className="text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/30 rounded-lg px-2 py-1 mb-2">
            Use first: {useFirst.map((batch) => batch.label).join(', ')}
          </p>
        )}

        {(pantry?.batches.length ?? 0) === 0 ? (
          <p className="text-xs text-slate-400">Nothing on the shelf yet. Log a batch when you put food up.</p>
        ) : (
          <ul className="space-y-2">
            {pantry?.batches.map((batch) => (
              <BatchCard
                key={batch.id}
                batch={batch}
                canEdit={canEdit}
                busy={useBatch.isPending || deleteBatch.isPending}
                onUse={() => useBatch.mutate(batch)}
                onEdit={() => setEditing(batch)}
                onDelete={() => deleteBatch.mutate(batch)}
              />
            ))}
          </ul>
        )}
      </section>

      {logOpen && (
        <BatchModal batch={null} goals={goalsQuery.data?.goals ?? []} onClose={() => setLogOpen(false)} />
      )}
      {editing && (
        <BatchModal batch={editing} goals={goalsQuery.data?.goals ?? []} onClose={() => setEditing(null)} />
      )}
    </div>
  );
}
