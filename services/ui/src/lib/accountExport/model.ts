/**
 * What is inside an opened export, for the read-only viewer and the restore.
 * The payload is NDJSON (services/gateway/account_export.py): a manifest, one
 * or more section lines per service, and an end line. A file without its end
 * line was cut short when it was made and is refused.
 */

export interface ExcludedItem {
  what: string;
  why: string;
}

export interface ExportManifest {
  kind: 'manifest';
  format: string;
  version: number;
  exported_at: string;
  subject: { username: string };
  exported_by: { username: string };
  services: string[];
  excluded: ExcludedItem[];
}

export type Row = Record<string, unknown>;

export interface OpenedExport {
  manifest: ExportManifest;
  /** service -> collection -> rows (sections of one service are joined). */
  services: Record<string, Record<string, Row[]>>;
  /** Parts the server could not read when the export was made. */
  failed: string[];
}

export class IncompleteExportError extends Error {
  constructor(message = 'This export was not finished when it was made. Make a new one.') {
    super(message);
    this.name = 'IncompleteExportError';
  }
}

/** Read the clear NDJSON stream of an opened export into the viewer's model. */
export async function readExport(stream: ReadableStream<Uint8Array>): Promise<OpenedExport> {
  const reader = stream.pipeThrough(new TextDecoderStream() as unknown as TransformStream<Uint8Array, string>).getReader();
  let buffer = '';
  let manifest: ExportManifest | null = null;
  let ended = false;
  const failed: string[] = [];
  const services: Record<string, Record<string, Row[]>> = {};

  const take = (line: string) => {
    if (!line.trim()) return;
    let record: Record<string, unknown>;
    try {
      record = JSON.parse(line) as Record<string, unknown>;
    } catch {
      throw new IncompleteExportError('This export is damaged inside (a line is not readable).');
    }
    if (ended) throw new IncompleteExportError('This export has data after its end.');
    if (record.kind === 'manifest') {
      if (manifest) throw new IncompleteExportError('This export has two beginnings.');
      manifest = record as unknown as ExportManifest;
      return;
    }
    if (!manifest) throw new IncompleteExportError('This export has no beginning.');
    if (record.kind === 'section') {
      const service = String(record.service || 'other');
      const collections = record.collections as Record<string, unknown> | undefined;
      if (!collections) return;
      const into = (services[service] ??= {});
      for (const [name, rows] of Object.entries(collections)) {
        if (!Array.isArray(rows)) continue;
        const list = (into[name] ??= []);
        for (const row of rows) if (row && typeof row === 'object') list.push(row as Row);
      }
      return;
    }
    if (record.kind === 'end') {
      ended = true;
      if (Array.isArray(record.failed)) failed.push(...record.failed.map(String));
    }
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let newline = buffer.indexOf('\n');
    while (newline >= 0) {
      take(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf('\n');
    }
  }
  take(buffer);
  if (!manifest) throw new IncompleteExportError('This export is empty.');
  if (!ended) throw new IncompleteExportError();
  const m = manifest as ExportManifest;
  if (m.format !== 'jarvis-account-export') throw new IncompleteExportError('This is not a Jarvis account export.');
  return { manifest: m, services, failed };
}

/**
 * Watch the export as it streams from the server: the device saves nothing
 * unless the stream ended with its end line. Throws IncompleteExportError at
 * the end otherwise. Passes every byte through unchanged.
 */
export function endChecker(): { stream: TransformStream<Uint8Array, Uint8Array>; failed: () => string[] } {
  const decoder = new TextDecoder();
  let tail = '';
  let failed: string[] = [];
  const stream = new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      tail = (tail + decoder.decode(chunk, { stream: true })).slice(-8192);
      controller.enqueue(chunk);
    },
    flush() {
      const lines = tail.trim().split('\n');
      try {
        const last = JSON.parse(lines[lines.length - 1]) as { kind?: string; failed?: unknown };
        if (last.kind !== 'end') throw new Error('no end');
        failed = Array.isArray(last.failed) ? last.failed.map(String) : [];
      } catch {
        throw new IncompleteExportError('The export stopped before it finished. Nothing was saved; try again.');
      }
    },
  });
  return { stream, failed: () => failed };
}

const SERVICE_LABELS: Record<string, string> = {
  identity: 'Profile and settings',
  health: 'Health',
  bible: 'Bible',
  geo: 'Places, trips and steps',
};

const COLLECTION_LABELS: Record<string, string> = {
  profile: 'Profile',
  avatar_image: 'Profile picture',
  theme: 'Theme',
  weather: 'Weather',
  calendar_settings: 'Calendar settings',
  notification_preferences: 'Notifications',
  nearby_settings: 'Nearby & offline',
  activity_sharing: 'Activity sharing',
  dashboard_widgets: 'Dashboard',
  workspace_sessions: 'Workspace sessions',
  workspace_chats: 'Workspace chats',
  workspace_chat_messages: 'Workspace chat messages',
  assistant_conversations: 'Conversations with Jarvis',
  feature_usage: 'Features used',
  screen_rules: 'Screen time rules',
  recommendation_lanes: 'Recommendations',
  screen_time_requests: 'Screen time requests',
  guard_requests: 'App requests',
  unlock_requests: 'Unlock requests',
  chore_checks: 'Chores',
  guardian_notes_written: 'Journal notes you wrote',
  account_history: 'Account history',
  preferences: 'Preferences',
  metrics: 'Measurements',
  sleep: 'Sleep',
  checkins: 'Check-ins',
  food: 'Food log',
  fasting: 'Fasting',
  goals: 'Goals',
  cycle_settings: 'Cycle settings',
  cycle_periods: 'Periods',
  cycle_logs: 'Cycle log',
  recipe_ratings: 'Recipe ratings',
  recipes_made: 'Recipes made',
  shares_given: 'Shared with',
  cheers_sent: 'Cheers sent',
  cheers_received: 'Cheers received',
  challenges_joined: 'Challenges',
  reading_state: 'Reading position',
  marks: 'Highlights and bookmarks',
  memory_verses: 'Memory verses',
  plan_enrollments: 'Reading plans',
  plan_reflections: 'Plan reflections',
  reading_events: 'Reading history',
  achievements: 'Achievements',
  game_scores: 'Game scores',
  study_sessions: 'Study sessions',
  study_session_turns: 'Study questions',
  plan_groups_joined: 'Plan groups',
  places: 'Saved places',
  place_reminders: 'Place reminders',
  trips: 'Trips',
  workouts: 'Workouts',
  daily_steps: 'Daily steps',
  visits: 'Places you stopped',
  location_history: 'Location trail',
};

export function serviceLabel(name: string): string {
  return SERVICE_LABELS[name] ?? name;
}

export function collectionLabel(name: string): string {
  return COLLECTION_LABELS[name] ?? name.replace(/_/g, ' ');
}

/** Never sent back on a restore: the server keeps them as history and would skip them anyway. */
const NOT_RESTORED = new Set(['location_history', 'visits']);

export interface ServiceSummary {
  service: string;
  label: string;
  collections: { name: string; label: string; count: number }[];
  total: number;
}

export function summarize(opened: OpenedExport): ServiceSummary[] {
  return Object.entries(opened.services).map(([service, collections]) => {
    const list = Object.entries(collections)
      .filter(([, rows]) => rows.length > 0)
      .map(([name, rows]) => ({ name, label: collectionLabel(name), count: rows.length }));
    return { service, label: serviceLabel(service), collections: list, total: list.reduce((n, c) => n + c.count, 0) };
  });
}

/** The restore, one request per service, in the order the server restores them. */
export function restoreParts(opened: OpenedExport): { service: string; collections: Record<string, Row[]> }[] {
  const order = opened.manifest.services?.length ? opened.manifest.services : Object.keys(opened.services);
  return order
    .filter((service) => opened.services[service])
    .map((service) => ({
      service,
      collections: Object.fromEntries(Object.entries(opened.services[service]).filter(([name]) => !NOT_RESTORED.has(name))),
    }))
    .filter((part) => Object.keys(part.collections).length > 0);
}

export interface Counts {
  add: number;
  update: number;
  conflict: number;
  unchanged: number;
  skipped: number;
}

export function addCounts(results: Record<string, Partial<Counts>>): Counts {
  const total: Counts = { add: 0, update: 0, conflict: 0, unchanged: 0, skipped: 0 };
  for (const counts of Object.values(results)) {
    for (const key of Object.keys(total) as (keyof Counts)[]) total[key] += Number(counts?.[key] ?? 0) || 0;
  }
  return total;
}

/** A plain reading of a value for the viewer: JSON text is shown as its contents. */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '';
  if (typeof value === 'object') {
    if ('$base64' in (value as Record<string, unknown>)) return '(picture)';
    return JSON.stringify(value);
  }
  if (typeof value === 'string' && /^[[{]/.test(value)) {
    try {
      const parsed = JSON.parse(value) as unknown;
      if (parsed && typeof parsed === 'object') {
        const text = JSON.stringify(parsed);
        return text === '{}' || text === '[]' ? '' : text;
      }
    } catch {
      /* plain text that starts with a bracket */
    }
  }
  return String(value);
}

/** A strength hint for the export password (a hint, not a rule beyond the minimum). */
export const MIN_EXPORT_PASSWORD = 10;

export function passwordStrength(password: string): { score: 0 | 1 | 2 | 3; label: string } {
  if (password.length < MIN_EXPORT_PASSWORD) return { score: 0, label: `Too short: use at least ${MIN_EXPORT_PASSWORD} characters` };
  const kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length;
  const words = password.trim().split(/\s+/).length;
  const unique = new Set(password.toLowerCase()).size;
  if (unique < 5) return { score: 1, label: 'Weak: too many repeated characters' };
  if (password.length >= 20 || (words >= 4 && password.length >= 16)) return { score: 3, label: 'Strong' };
  if (password.length >= 14 || kinds >= 3) return { score: 2, label: 'Good: a longer sentence is stronger' };
  return { score: 1, label: 'Weak: add a few more words' };
}

export function exportPasswordProblem(password: string, confirm: string): string | null {
  if (password.length < MIN_EXPORT_PASSWORD) return `Use at least ${MIN_EXPORT_PASSWORD} characters.`;
  if (passwordStrength(password).score < 2) return 'Choose a stronger password: a short sentence works well.';
  if (password !== confirm) return 'The two passwords do not match.';
  return null;
}

export function exportFileName(username: string, now = new Date()): string {
  const day = now.toISOString().slice(0, 10);
  const safe = username.replace(/[^A-Za-z0-9_-]/g, '') || 'account';
  return `jarvis-export-${safe}-${day}.jarvis-export`;
}
