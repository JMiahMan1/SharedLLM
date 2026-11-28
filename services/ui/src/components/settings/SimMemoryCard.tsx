import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Brain, ChevronRight, RefreshCcw, Sparkles } from 'lucide-react';
import toast from 'react-hot-toast';
import { errorText } from '../../lib/errorText';
import { filterSimProblems, simSummary, type SimHarnessFilter, type SimScopeFilter } from '../../lib/simMemory';
import { api } from '../../services/api';
import type { SimHarness, SimProblem, SimStatus } from '../../types/api';

const SCOPE_FILTERS: { id: SimScopeFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'system', label: 'System (Shared)' },
  { id: 'user', label: 'My User SIM' },
];

const HARNESS_FILTERS: { id: SimHarnessFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'raven', label: 'Raven' },
  { id: 'workspace', label: 'Workspace' },
  { id: 'satellite', label: 'Voice & Chat' },
];

const HARNESS_LABEL: Record<SimHarness, string> = {
  raven: 'Raven',
  workspace: 'Workspace',
  satellite: 'Voice & Chat',
};

const HARNESS_TONE: Record<SimHarness, string> = {
  raven: 'border-indigo-500/40 bg-indigo-500/10 text-indigo-200',
  workspace: 'border-sky-500/40 bg-sky-500/10 text-sky-200',
  satellite: 'border-teal-500/40 bg-teal-500/10 text-teal-200',
};

const STATUS_META: Record<SimStatus, { label: string; tone: string; blurb: string }> = {
  portable: {
    label: 'Portable',
    tone: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
    blurb: 'Helped at least two model families with few regressions',
  },
  verified: {
    label: 'Verified',
    tone: 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200',
    blurb: 'Improved on one model family',
  },
  model_specific: {
    label: 'Model-specific',
    tone: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
    blurb: 'Shown only to the family it helped',
  },
  candidate: {
    label: 'Candidate',
    tone: 'border-slate-500/40 bg-slate-500/10 text-slate-300',
    blurb: 'Recorded, not yet verified',
  },
  retired: {
    label: 'Retired',
    tone: 'border-rose-500/40 bg-rose-500/10 text-rose-200',
    blurb: 'Regressed or superseded; never injected',
  },
};

/**
 * Self-Improvement Memory: the problem-grouped lessons the harnesses carry,
 * split into System (shared) and the reader's own User SIM. Read-only unless
 * an admin runs the consolidation pass; every failure is shown calmly.
 */
export default function SimMemoryCard({ isAdmin = false }: { isAdmin?: boolean }) {
  const queryClient = useQueryClient();
  const [scope, setScope] = useState<SimScopeFilter>('all');
  const [harness, setHarness] = useState<SimHarnessFilter>('all');
  const [showRetired, setShowRetired] = useState(false);

  // One read of the whole ledger: the pills stay honest while the filters
  // below change instantly, and the pass is small (the endpoint caps at 1000).
  const simQuery = useQuery({
    queryKey: ['trainer', 'sim'],
    queryFn: () => api.trainerSimProblems({ include_retired: true }),
  });

  const consolidate = useMutation({
    mutationFn: () => api.trainerSimConsolidate(),
    onSuccess: (result) => {
      toast.success(
        `SIM consolidated: ${result.updated} updated, ${result.promoted.length} portable, ${result.retired.length} retired`,
      );
      queryClient.invalidateQueries({ queryKey: ['trainer', 'sim'] });
    },
    onError: (error) => toast.error(errorText(error, 'SIM consolidation failed')),
  });

  const problems = useMemo(() => simQuery.data?.problems ?? [], [simQuery.data]);
  const summary = useMemo(() => simSummary(problems), [problems]);
  const filtered = useMemo(
    () => filterSimProblems(problems, { scope, harness, showRetired }),
    [problems, scope, harness, showRetired],
  );

  return (
    <section className="rounded-2xl border border-white/10 bg-white/5 p-5" data-testid="sim-memory-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-lg font-black uppercase tracking-tight text-white">
            <Brain size={16} className="text-fuchsia-300" /> Self-Improvement Memory
          </h3>
          <p className="mt-1 max-w-2xl text-sm text-slate-400">
            Lessons every harness learns from its own mistakes, grouped by problem. System SIM is shared;
            your User SIM stays yours. Portability tracks whether a rule helps other models or only one.
          </p>
        </div>
        {isAdmin && (
          <button
            type="button"
            onClick={() => consolidate.mutate()}
            disabled={consolidate.isPending}
            data-testid="sim-consolidate"
            className="flex min-h-11 items-center gap-2 rounded-xl border border-fuchsia-500/30 bg-fuchsia-500/10 px-4 text-[10px] font-black uppercase tracking-widest text-fuchsia-200 disabled:opacity-40"
          >
            <Sparkles size={14} /> {consolidate.isPending ? 'Consolidating…' : 'Consolidate SIM'}
          </button>
        )}
      </div>

      {simQuery.isError ? (
        <div role="status" data-testid="sim-error" className="mt-4 rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-xs text-amber-200">
          <p className="font-bold">SIM memory is unavailable right now.</p>
          <p className="mt-1 text-[11px] text-amber-200/80">
            {errorText(simQuery.error, 'The SIM endpoint did not answer')}
          </p>
          <button
            type="button"
            onClick={() => simQuery.refetch()}
            className="mt-2 flex min-h-9 items-center gap-1.5 rounded-lg border border-amber-500/30 px-3 text-[10px] font-black uppercase tracking-widest"
          >
            <RefreshCcw size={12} /> Try again
          </button>
        </div>
      ) : simQuery.isLoading ? (
        <p className="mt-4 text-sm text-slate-500">Loading SIM lessons…</p>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            <SummaryPill label="Total lessons" value={summary.total} />
            <SummaryPill label="Portable" value={summary.portable} tone="text-emerald-300" />
            <SummaryPill label="System" value={summary.system} tone="text-violet-300" />
            <SummaryPill label="Personal" value={summary.user} tone="text-cyan-300" />
            <SummaryPill label="Retired" value={summary.retired} tone="text-rose-300" />
          </div>

          <div className="mt-4 flex flex-wrap items-end gap-3">
            <label className="text-[10px] font-black uppercase tracking-widest text-slate-500">
              Scope
              <select
                aria-label="SIM scope"
                value={scope}
                onChange={(event) => setScope(event.target.value as SimScopeFilter)}
                className="mt-1 block min-h-9 rounded-lg border border-white/10 bg-black/30 px-2 text-xs font-normal normal-case tracking-normal text-white"
              >
                {SCOPE_FILTERS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-[10px] font-black uppercase tracking-widest text-slate-500">
              Harness
              <select
                aria-label="SIM harness"
                value={harness}
                onChange={(event) => setHarness(event.target.value as SimHarnessFilter)}
                className="mt-1 block min-h-9 rounded-lg border border-white/10 bg-black/30 px-2 text-xs font-normal normal-case tracking-normal text-white"
              >
                {HARNESS_FILTERS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex min-h-9 items-center gap-2 text-[11px] text-slate-300">
              <input
                type="checkbox"
                checked={showRetired}
                onChange={(event) => setShowRetired(event.target.checked)}
                className="h-4 w-4 rounded border-white/20 bg-black/30"
              />
              Show retired
            </label>
            <span className="ml-auto text-[10px] font-black uppercase tracking-widest text-slate-500">
              {filtered.length} shown
            </span>
          </div>

          {problems.length === 0 ? (
            <p className="mt-4 rounded-xl border border-dashed border-white/10 p-4 text-sm text-slate-500" data-testid="sim-empty">
              No SIM lessons yet. They appear here as the harnesses record what worked and what did not.
            </p>
          ) : filtered.length === 0 ? (
            <p className="mt-4 rounded-xl border border-dashed border-white/10 p-4 text-sm text-slate-500" data-testid="sim-empty-filtered">
              No lessons match these filters.
            </p>
          ) : (
            <ul className="mt-4 space-y-3">
              {filtered.map((problem) => (
                <SimProblemRow key={`${problem.scope}-${problem.problem_id}`} problem={problem} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function SummaryPill({ label, value, tone = 'text-white' }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-black/20 p-3" data-testid={`sim-summary-${label.toLowerCase().replace(/\s+/g, '-')}`}>
      <p className="text-[10px] font-black uppercase tracking-widest text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-black ${tone}`}>{value}</p>
    </div>
  );
}

function SimProblemRow({ problem }: { problem: SimProblem }) {
  const [open, setOpen] = useState(false);
  const status = STATUS_META[problem.status] ?? STATUS_META.candidate;
  return (
    <li className="rounded-xl border border-white/10 bg-black/20 p-3" data-testid="sim-problem">
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] font-black uppercase tracking-wider ${
            problem.scope === 'system'
              ? 'border-violet-500/40 bg-violet-500/10 text-violet-200'
              : 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
          }`}
          data-testid="sim-scope-badge"
        >
          {problem.scope === 'system' ? 'System' : 'User'}
        </span>
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] font-black uppercase tracking-wider ${HARNESS_TONE[problem.harness] ?? HARNESS_TONE.satellite}`}
          data-testid="sim-harness-badge"
        >
          {HARNESS_LABEL[problem.harness] ?? problem.harness}
        </span>
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] font-black uppercase tracking-wider ${status.tone}`}
          title={status.blurb}
          data-testid="sim-status-badge"
        >
          {status.label}
        </span>
        {problem.requires_admin && (
          <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-black uppercase tracking-wider text-amber-200">
            Admins only
          </span>
        )}
        <span className="ml-auto text-[10px] text-slate-500">
          {problem.updated_at ? new Date(problem.updated_at * 1000).toLocaleDateString() : '—'}
        </span>
      </div>

      <p className="mt-2 text-sm font-semibold leading-relaxed text-slate-100">{problem.edit_or_rule || problem.signature}</p>

      {problem.rationale && (
        <div className="mt-1.5">
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="flex items-center gap-1 text-[10px] font-black uppercase tracking-widest text-slate-500 hover:text-slate-300"
          >
            <ChevronRight size={11} className={`transition-transform ${open ? 'rotate-90' : ''}`} /> Why this lesson
          </button>
          {open && <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-slate-400">{problem.rationale}</p>}
        </div>
      )}

      {(problem.verified_models.length > 0 || problem.regressed_models.length > 0) && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5" data-testid="sim-model-chips">
          {problem.verified_models.map((family) => (
            <span key={`v-${family}`} className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-200">
              ✓ {family}
            </span>
          ))}
          {problem.regressed_models.map((family) => (
            <span key={`r-${family}`} className="rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-[10px] text-rose-200">
              ↓ {family}
            </span>
          ))}
        </div>
      )}

      <p className="mt-2 text-[10px] text-slate-500">
        {problem.category || 'uncategorised'} · {problem.harness === 'satellite' ? 'Voice & Chat' : problem.harness} harness
        {problem.retrieval_count ? ` · used ${problem.retrieval_count}×` : ''}
        {problem.tags.length ? ` · ${problem.tags.slice(0, 5).join(', ')}` : ''}
      </p>
    </li>
  );
}
