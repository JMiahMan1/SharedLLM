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

/** A percentage as the tab shows it. */
export function passRateLabel(rate: number): string {
  const pct = Math.round((Number.isFinite(rate) ? rate : 0) * 100);
  return `${pct}%`;
}

/** How a proposal should look: failures first, then memory, then UI ideas. */
export function proposalTone(kind: string): string {
  if (kind === 'model_unavailable') return 'border-rose-500/30 bg-rose-500/10 text-rose-300';
  if (kind.startsWith('curriculum_') || kind === 'tool_doc') {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-300';
  }
  if (kind.startsWith('lesson_')) return 'border-indigo-500/30 bg-indigo-500/10 text-indigo-300';
  if (kind === 'ui_gap') return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300';
  return 'border-white/10 bg-white/5 text-slate-300';
}
