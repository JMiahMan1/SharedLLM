/**
 * The Jarvis Trainer's shapes, and the small pure helpers the Lab's Training tab
 * renders with. Kept out of the component so they can be unit-tested without a
 * DOM, and out of `services/api.ts` so the transport stays a thin wrapper.
 */

export interface TrainerFeature {
  id: string;
  kind: 'route' | 'tool' | 'page' | string;
  service: string;
  method: string;
  path: string;
  name: string;
  summary: string;
  use_case: string;
  tags: string[];
  auth: string;
}

export interface TrainerCatalog {
  generated_at: string;
  fingerprint: string;
  counts: {
    total: number;
    by_service: Record<string, number>;
    by_kind: Record<string, number>;
    by_tag: Record<string, number>;
  };
  features: TrainerFeature[];
}

export interface TrainerCase {
  id: string;
  bucket: string;
  title: string;
  prompt: string;
  checks: Record<string, unknown>;
  source: string;
  difficulty: number;
  max_tokens: number;
  tags: string[];
}

export interface TrainerCurriculum {
  summary: { total: number; by_bucket: Record<string, number>; by_difficulty: Record<string, number> };
  buckets: string[];
  cases: TrainerCase[];
}

export interface TrainerBucketResult {
  cases: number;
  passed: number;
  pass_rate: number;
  failed_ids?: string[];
}

export interface TrainerCaseResult {
  id: string;
  bucket: string;
  title: string;
  passed: boolean;
  score: number;
  elapsed_s: number;
  failed_checks: string[];
  answer: string;
  error?: string | null;
}

export interface TrainerReport {
  id: string;
  started_at: string;
  finished_at: string;
  model: string;
  elapsed_s: number;
  stopped_reason?: string | null;
  totals: { cases: number; requested: number; passed: number; pass_rate: number; answer_chars?: number };
  by_bucket: Record<string, TrainerBucketResult>;
  cases: TrainerCaseResult[];
}

export interface TrainerReportSummary {
  id: string;
  started_at: string | null;
  model: string | null;
  totals: TrainerReport['totals'];
  stopped_reason: string | null;
}

export interface TrainerProposal {
  id: string;
  kind: string;
  title: string;
  detail: string;
  priority: number;
  evidence: string[];
  action: string;
}

/** The machine that hosts the model (Alpaca), as `GET /api/trainer/hardware` reports it. */
export interface TrainerHardware {
  reachable: boolean;
  ready: boolean;
  reasons: string[];
  free_vram_mb?: number | null;
  total_vram_mb?: number | null;
  context_length?: number | null;
  base_url?: string;
  dashboard_url?: string | null;
  errors?: string[];
  active_requests?: number;
  queued_requests?: number;
  loaded_models?: Array<{ model?: string | null; backend_model?: string | null }>;
  slots?: { total: number; available: number; status?: string | null; message?: string | null };
  models?: { chosen: string; total: number; trainer_model_missing?: boolean };
  lora?: { total: number; adapters?: Array<{ id?: number; path?: string | null; scale?: number | null }> };
}

/** Alpaca's benchmark results, as `GET /api/trainer/benchmarks` reports them. */
export interface TrainerBenchmarks {
  total: number;
  chosen_model: string;
  directories: Record<string, string>;
  weak_categories: Array<{
    category: string;
    score: number;
    tests_run: number;
    tests_passed: number;
    letter?: string | null;
  }>;
  results: Array<{
    suite: string;
    model: string;
    generated_at: string | null;
    average_score: number | null;
    categories: Record<string, { score?: number | null; tests_run?: number | null }>;
  }>;
}

/** One line for the model's report card: its weakest graded categories. */
export function benchmarkLine(benchmarks: TrainerBenchmarks | null | undefined): string {
  if (!benchmarks) return 'no benchmark results yet';
  if (!benchmarks.total) return 'no benchmark results yet';
  const weak = (benchmarks.weak_categories ?? []).slice(0, 3);
  if (!weak.length) return 'every graded category is above 60';
  return weak.map((item) => `${item.category} ${item.score}/100`).join(' · ');
}

/** Alpaca's model inventory, as `GET /api/trainer/models` reports it. */
export interface TrainerModels {
  total: number;
  chosen: string;
  trainer_model_setting: string;
  trainer_model_missing: boolean;
  reason?: string;
  error?: string;
  models: Array<{
    name: string;
    size_gb: number;
    context_length: number | null;
    capabilities: string[];
    complete: boolean;
    router_status: string;
  }>;
}

export interface TrainerBacklog {
  generated_at: string | null;
  eval_report_id: string | null;
  proposals: TrainerProposal[];
  lessons: string[];
}

export interface TrainerCycle {
  status: string;
  curriculum: TrainerCurriculum['summary'];
  eval: { report_id: string; model: string; totals: TrainerReport['totals']; by_bucket: Record<string, TrainerBucketResult> };
  dream: Record<string, unknown>;
  proposals: TrainerProposal[];
  lessons: string[];
  errors: string[];
}

/** One row per bucket, ordered worst first: the failures are what to read. */
export function bucketBars(byBucket: Record<string, TrainerBucketResult>) {
  return Object.entries(byBucket)
    .map(([bucket, stats]) => ({
      bucket,
      cases: stats.cases,
      passed: stats.passed,
      passRate: Math.round((stats.pass_rate ?? 0) * 100),
    }))
    .sort((a, b) => a.passRate - b.passRate || a.bucket.localeCompare(b.bucket));
}

/** What the catalogue is made of, for the two-line summary at the top. */
export function summariseCatalog(catalog: TrainerCatalog) {
  const services = Object.entries(catalog.counts.by_service)
    .map(([service, count]) => ({ service, count }))
    .sort((a, b) => b.count - a.count);
  return {
    total: catalog.counts.total,
    services,
    tools: catalog.counts.by_kind.tool ?? 0,
    pages: catalog.counts.by_kind.page ?? 0,
    topTags: Object.entries(catalog.counts.by_tag)
      .map(([tag, count]) => ({ tag, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 6),
  };
}

/** Bytes of free VRAM as a short label, for the host card. */
export function freeVramLabel(hardware: TrainerHardware | null | undefined): string {
  const free = hardware?.free_vram_mb;
  if (!hardware?.reachable) return 'no host';
  if (typeof free !== 'number') return 'unknown';
  return `${(free / 1024).toFixed(1)} GB free`;
}

/** Whether a pass can run now, and the one line that explains it. */
export function hardwareVerdict(hardware: TrainerHardware | null | undefined): { ready: boolean; label: string } {
  if (!hardware) return { ready: false, label: 'asking the model host…' };
  if (!hardware.reachable) {
    return { ready: false, label: hardware.reasons?.[0] || 'the model host did not answer' };
  }
  if (hardware.ready) {
    const slots = hardware.slots;
    return {
      ready: true,
      label: slots ? `${slots.available} of ${slots.total} slots free` : 'the host is idle',
    };
  }
  return { ready: false, label: hardware.reasons?.[0] || 'the host is busy' };
}

/** A percentage as the tab shows it. */
export function passRateLabel(rate: number): string {
  const pct = Math.round((Number.isFinite(rate) ? rate : 0) * 100);
  return `${pct}%`;
}

/** How a proposal should look: failures first, then memory, then UI ideas. */
export function proposalTone(kind: string): string {
  if (kind === 'model_unavailable') return 'border-rose-500/30 bg-rose-500/10 text-rose-300';
  if (kind.startsWith('curriculum_') || kind === 'tool_doc' || kind === 'teacher_gap') {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
  }
  if (kind.startsWith('lesson_')) return 'border-indigo-500/30 bg-indigo-500/10 text-indigo-300';
  if (kind === 'ui_gap') return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300';
  return 'border-white/10 bg-white/5 text-slate-300';
}

// --- Teacher traces: what strong cloud models did on this repository --------

export interface TeacherScore {
  report_id: string;
  started_at: string;
  model: string;
  cases: number;
  passed: number;
  pass_rate: number;
}

export interface TeacherExport {
  format: string;
  paths: Record<string, string>;
  train_rows: number;
  eval_rows: number;
  skipped: Record<string, number>;
  created_at: string;
}

export interface TeacherSummary {
  traces: {
    total: number;
    by_status: Record<string, number>;
    by_outcome: Record<string, number>;
    by_source: Record<string, number>;
    by_location: Record<string, number>;
    by_model: Record<string, number>;
    masked_values: number;
    dropped_steps: number;
  };
  lessons: { written: number; last_written_at: string | null; eligible: number };
  eval_cases: number;
  scores: TeacherScore[];
  exports: TeacherExport[];
  scrub_version: string;
}

export interface TeacherTraceRow {
  id: string;
  source: string;
  model: string;
  model_location: string;
  started_at: string | null;
  imported_at: string | null;
  goal: string;
  outcome: string;
  outcome_evidence: string[];
  step_count: number;
  tools: string[];
  areas: string[];
  scrub_status: string;
  scrub_masked: number;
  scrub_dropped_steps: number;
  drop_reason: string | null;
  lesson_id: string | null;
  case_id: string | null;
}

export interface TeacherLessonsResult {
  written: { id: string; lesson_id: string; rule: string }[];
  skipped: { id: string; reason: string }[];
  failed: { id: string; error: string }[];
  dry_run: boolean;
}

/** How a trace's scrub status reads: dropped is a warning, masked is normal. */
export function scrubTone(status: string): string {
  if (status === 'dropped') return 'border-rose-500/30 bg-rose-500/10 text-rose-300';
  if (status === 'masked') return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
  if (status === 'clean') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300';
  return 'border-white/10 bg-white/5 text-slate-300';
}

/** A dropped trace shows why, never what: the reason names a rule, not a value. */
export function scrubLabel(
  row: Pick<TeacherTraceRow, 'scrub_status' | 'scrub_masked' | 'scrub_dropped_steps' | 'drop_reason'>,
): string {
  if (row.scrub_status === 'dropped') return `dropped: ${(row.drop_reason || 'unknown').replace(/_/g, ' ')}`;
  const parts = [row.scrub_status];
  if (row.scrub_masked) parts.push(`${row.scrub_masked} masked`);
  if (row.scrub_dropped_steps) {
    parts.push(`${row.scrub_dropped_steps} step${row.scrub_dropped_steps === 1 ? '' : 's'} removed`);
  }
  return parts.join(' · ');
}

/** How an outcome reads: only a checked success is green. */
export function outcomeTone(outcome: string): string {
  if (outcome === 'success') return 'text-emerald-300';
  if (outcome === 'failure') return 'text-rose-300';
  return 'text-slate-400';
}

/** The local model's teacher-task score: latest, and the change since the first run. */
export function teacherTrend(scores: TeacherScore[] | null | undefined): {
  latest: string;
  change: string;
  runs: number;
} {
  const rows = scores ?? [];
  if (!rows.length) return { latest: '-', change: 'not evaluated yet', runs: 0 };
  const first = rows[0].pass_rate;
  const last = rows[rows.length - 1].pass_rate;
  const delta = Math.round((last - first) * 100);
  const change =
    rows.length < 2
      ? 'first run'
      : delta === 0
        ? 'no change since the first run'
        : `${delta > 0 ? '+' : ''}${delta} points since the first run`;
  return { latest: passRateLabel(last), change, runs: rows.length };
}
