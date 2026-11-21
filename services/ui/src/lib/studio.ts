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
