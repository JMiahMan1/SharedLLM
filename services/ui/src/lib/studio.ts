/**
 * The Workspace Studio's shapes and the small decisions its panel renders with:
 * which steps a detected stack offers, what a run's outcome means, and how a
 * preview URL should read. Kept free of React so they can be unit-tested.
 */

export interface StudioStep {
  name: string;
  command: string;
  detail: string;
}

export interface StudioProfile {
  stack: string;
  label: string;
  language: string;
  entry: string;
  preview_port: number | null;
  notes: string[];
  steps: Record<string, StudioStep>;
  all_stacks?: string[];
}

export interface StudioRunResult {
  action: string;
  stack: string;
  command: string;
  ok: boolean;
  returncode: number | null;
  duration_s: number;
  stdout: string;
  stderr: string;
  message: string;
}

export interface StudioPreview {
  ok: boolean;
  stack?: string;
  command?: string;
  container_port?: number;
  host_port?: number | null;
  url?: string;
  answered?: boolean;
  log?: string;
  message?: string;
}

export interface StudioAsset {
  kind: 'image' | 'audio' | string;
  path: string;
  prompt?: string;
  model?: string;
  size?: string;
  bytes?: number;
  made_at?: string;
}

export interface StudioBuilt {
  kind: string;
  path: string;
  title: string;
  files: string[];
  slides: number;
  assets: string[];
  message: string;
}

/** One probe's answer inside a shell diagnosis. */
export interface StudioProbe {
  name: string;
  command: string;
  why: string;
  ok: boolean;
  returncode: number | null;
  output: string;
  duration_s?: number;
}

/** What `POST /api/studio/diagnose` reports. */
export interface StudioDiagnosis {
  workspace_id: string;
  ok: boolean;
  failed: string[];
  hint: string;
  probes: StudioProbe[];
  profile?: StudioProfile | null;
  profile_error?: string;
}

/** What `POST /api/studio/verify` reports: shell, a real program, and the tests. */
export interface StudioVerdict {
  ok: boolean;
  stack: string;
  label: string;
  shell: { ok: boolean; failed: string[] };
  program: {
    ok: boolean;
    skipped?: boolean;
    command?: string;
    output?: string;
    message?: string;
  };
  tests:
    | {
        ok: boolean;
        available: boolean;
        command?: string;
        stdout?: string;
        stderr?: string;
        message?: string;
      }
    | null;
  hint: string;
}

/** One side of an STL edit. */
export interface StlSummary {
  path: string;
  format: string;
  triangles: number;
  vertices: number;
  size: { x: number; y: number; z: number };
  longest: number;
  volume: number;
  area: number;
  watertight: boolean;
  open_edges: number;
  degenerate: number;
  units: string;
  warnings: string[];
}

/** What `POST /api/studio/stl/transform` reports. */
export interface StlEdit {
  source: string;
  path: string;
  format: string;
  applied: Record<string, unknown>;
  before: StlSummary;
  after: StlSummary;
  warnings: string[];
}


/** The actions worth a button, in the order a project needs them. */
export const STEP_ORDER = ['install', 'test', 'build', 'lint'] as const;

export function stepsFor(profile: StudioProfile | null | undefined) {
  if (!profile) return [];
  return STEP_ORDER.filter((name) => Boolean(profile.steps?.[name])).map((name) => ({
    ...profile.steps[name],
    name,
  }));
}

/** One line describing what the workspace is, for the panel header. */
export function stackLine(profile: StudioProfile | null | undefined): string {
  if (!profile) return 'detecting…';
  if (profile.stack === 'unknown') return 'no project detected';
  const port = profile.preview_port ? ` · preview on ${profile.preview_port}` : '';
  return `${profile.label}${port}`;
}

/** How a finished step reads: the outcome first, the command second. */
export function runSummary(result: StudioRunResult | null | undefined): string {
  if (!result) return '';
  const exit = result.returncode === null ? '' : ` (exit ${result.returncode})`;
  return `${result.action} ${result.ok ? 'passed' : 'failed'}${exit} in ${result.duration_s.toFixed(1)}s`;
}

/** A preview's state as the panel shows it. */
export function previewState(preview: StudioPreview | null | undefined): {
  live: boolean;
  label: string;
} {
  if (!preview) return { live: false, label: '' };
  if (preview.ok && preview.url) return { live: true, label: preview.url };
  return { live: false, label: preview.message || 'the server did not answer' };
}

/** "1 -> 120 longest (fit_longest, centered)": what changed, with both numbers. */
export function stlEditLine(edit: StlEdit | null | undefined): string {
  if (!edit) return '';
  const before = Number(edit.before?.longest ?? 0);
  const after = Number(edit.after?.longest ?? 0);
  const done = Object.keys(edit.applied || {});
  const suffix = done.length ? ` (${done.join(', ')})` : '';
  return `${trimNumber(before)} -> ${trimNumber(after)} longest${suffix}`;
}

/** The size as a person reads it: 120 x 80 x 12.5. */
export function stlSizeLine(summary: StlSummary | null | undefined): string {
  const size = summary?.size;
  if (!size) return '';
  return `${trimNumber(size.x)} x ${trimNumber(size.y)} x ${trimNumber(size.z)}`;
}

/** What is wrong with a mesh, or that nothing is. */
export function stlVerdict(summary: StlSummary | null | undefined): string {
  if (!summary) return '';
  if (!summary.warnings?.length) {
    return `closed solid - ${summary.triangles.toLocaleString()} triangles - ${summary.units}`;
  }
  return summary.warnings.join('; ');
}

/** The shell check as one line: healthy, or failing with the cause. */
export function diagnosisLine(diagnosis: StudioDiagnosis | null | undefined): string {
  if (!diagnosis) return '';
  if (diagnosis.ok) return 'the sandbox shell is healthy';
  const failed = (diagnosis.failed || []).join(', ') || 'something';
  return `${failed} failed - ${diagnosis.hint}`;
}

/** The three-part verdict as one line, so the panel can show it at a glance. */
export function verifyLine(verdict: StudioVerdict | null | undefined): string {
  if (!verdict) return '';
  const mark = (label: string, ok: boolean) => `${ok ? 'ok' : 'FAILED'} ${label}`;
  const tests = verdict.tests
    ? mark(verdict.tests.available ? 'tests' : 'no tests', verdict.tests.ok)
    : '';
  const parts = [
    mark('shell', verdict.shell?.ok ?? false),
    mark(verdict.program?.skipped ? 'program (skipped)' : 'program', verdict.program?.ok ?? false),
    tests,
  ].filter(Boolean);
  return `${parts.join(' | ')} - ${verdict.hint}`;
}

/** 12.5 rather than 12.500000001; whole numbers keep no decimal point. */
function trimNumber(value: number): string {
  if (!Number.isFinite(value)) return '0';
  return String(Math.round(value * 100) / 100);
}
