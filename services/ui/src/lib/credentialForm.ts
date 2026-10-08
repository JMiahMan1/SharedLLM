/**
 * Rules for editing a user's service logins, shared by the Admin "Edit user"
 * dialog and the integration cards in Settings.
 *
 * Two rules the server also enforces:
 * - A blank field means "keep what is saved", never "erase it". Secrets are
 *   never sent to the browser, so every form opens with them blank; treating
 *   blank as erase wiped saved passwords whenever anyone saved for any reason.
 * - Removing a login is explicit (`clear_fields`), and audited.
 * And one the forms enforce: an integration is saved whole or not at all, so
 * a half-filled login (a URL with no password) cannot be submitted.
 */

export interface CredentialField {
  key: string;
  label: string;
  secret?: boolean;
}

export interface Integration {
  id: string;
  name: string;
  fields: CredentialField[];
  /** Ways to be complete: any one list fully present is enough. */
  complete: string[][];
}

export const INTEGRATIONS: Integration[] = [
  {
    id: 'home_assistant',
    name: 'Home Assistant',
    fields: [
      { key: 'ha_url', label: 'Home Assistant URL' },
      { key: 'ha_token', label: 'Home Assistant Token', secret: true },
    ],
    complete: [['ha_url', 'ha_token']],
  },
  {
    id: 'nextcloud',
    name: 'Nextcloud',
    fields: [
      { key: 'nextcloud_url', label: 'Nextcloud URL' },
      { key: 'nextcloud_user', label: 'Nextcloud Username' },
      { key: 'nextcloud_pass', label: 'Nextcloud Password', secret: true },
    ],
    complete: [['nextcloud_url', 'nextcloud_user', 'nextcloud_pass']],
  },
  {
    id: 'github',
    name: 'GitHub',
    fields: [
      { key: 'github_url', label: 'GitHub URL' },
      { key: 'github_user', label: 'GitHub Username' },
      { key: 'github_token', label: 'GitHub Token', secret: true },
    ],
    complete: [['github_user', 'github_token']],
  },
  {
    id: 'gitlab',
    name: 'GitLab',
    fields: [
      { key: 'gitlab_url', label: 'GitLab URL' },
      { key: 'gitlab_user', label: 'GitLab Username' },
      { key: 'gitlab_token', label: 'GitLab Token', secret: true },
    ],
    complete: [['gitlab_url', 'gitlab_user', 'gitlab_token']],
  },
  {
    id: 'audiobookshelf',
    name: 'Audiobookshelf',
    fields: [
      { key: 'audiobookshelf_url', label: 'Audiobookshelf URL' },
      { key: 'audiobookshelf_user', label: 'Audiobookshelf Username' },
      { key: 'audiobookshelf_pass', label: 'Audiobookshelf Password', secret: true },
      { key: 'audiobookshelf_api_key', label: 'Audiobookshelf API Key', secret: true },
    ],
    complete: [
      ['audiobookshelf_url', 'audiobookshelf_api_key'],
      ['audiobookshelf_url', 'audiobookshelf_user', 'audiobookshelf_pass'],
    ],
  },
  {
    id: 'music_assistant',
    name: 'Music Assistant',
    fields: [
      { key: 'mass_url', label: 'Music Assistant URL' },
      { key: 'mass_token', label: 'Music Assistant Token', secret: true },
    ],
    complete: [['mass_url', 'mass_token']],
  },
];

const SECRET_KEYS = new Set(INTEGRATIONS.flatMap((i) => i.fields.filter((f) => f.secret).map((f) => f.key)));

export function isSecretKey(key: string): boolean {
  return SECRET_KEYS.has(key) || /(_pass|_token|_api_key|password)$/.test(key);
}

type Values = Record<string, unknown>;

/** Values a form starts from (household URLs, a suggested username). */
export type Defaults = Record<string, string | null | undefined>;

/**
 * Fill blank address fields from the household defaults, and suggest the
 * Nextcloud username from the account's own (they are the same here).
 */
export function prefill<T extends Values>(values: T, defaults: Defaults, username?: string): { values: T; prefilled: Defaults } {
  const out: Values = { ...values };
  const prefilled: Defaults = {};
  const suggestions: Defaults = { ...defaults, nextcloud_user: username || undefined };
  for (const [key, suggestion] of Object.entries(suggestions)) {
    if (!suggestion || !(key in out)) continue;
    if (typeof out[key] === 'string' && (out[key] as string).trim() === '') {
      out[key] = suggestion;
      prefilled[key] = suggestion;
    }
  }
  return { values: out as T, prefilled };
}

function present(key: string, values: Values, saved: ReadonlySet<string>): boolean {
  const value = values[key];
  if (typeof value === 'string' && value.trim() !== '') return true;
  return isSecretKey(key) && saved.has(key);
}

/**
 * True when someone has started on (or already has) this integration. A field
 * still holding its prefilled value does not count: the household URL alone
 * is not a login anyone asked for.
 */
export function inUse(integration: Integration, values: Values, saved: ReadonlySet<string>, prefilled: Defaults = {}): boolean {
  return integration.fields.some((f) => {
    if (prefilled[f.key] !== undefined && values[f.key] === prefilled[f.key]) return false;
    return present(f.key, values, saved);
  });
}

/**
 * Labels still needed for ``integration`` to be complete, or [] when it is
 * complete or untouched. Picks the closest way to complete it.
 */
export function missingFields(integration: Integration, values: Values, saved: ReadonlySet<string>, prefilled: Defaults = {}): string[] {
  if (!inUse(integration, values, saved, prefilled)) return [];
  const gaps = integration.complete.map((keys) => keys.filter((k) => !present(k, values, saved)));
  if (gaps.some((g) => g.length === 0)) return [];
  const closest = gaps.reduce((a, b) => (b.length < a.length ? b : a));
  return closest.map((k) => integration.fields.find((f) => f.key === k)?.label ?? k);
}

/** Every incomplete integration in a form, as "Nextcloud: Nextcloud Password". */
export function validateIntegrations(values: Values, saved: ReadonlySet<string>, only?: string[], prefilled: Defaults = {}): string[] {
  return INTEGRATIONS.filter((i) => !only || only.includes(i.id)).flatMap((i) => {
    const missing = missingFields(i, values, saved, prefilled);
    return missing.length ? [`${i.name}: ${missing.join(', ')}`] : [];
  });
}

/**
 * The PATCH body for a credentials form: only fields with something in them
 * (blank means keep), plus the integrations the user chose to remove.
 */
export function credentialPayload(
  values: Values,
  keys: string[],
  clear: string[] = [],
  context: { saved?: ReadonlySet<string>; prefilled?: Defaults } = {},
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  const saved = context.saved ?? new Set<string>();
  for (const key of keys) {
    // An integration nobody touched keeps its prefilled URL out of the save.
    const integration = integrationFor(key);
    if (integration && context.prefilled && !inUse(integration, values, saved, context.prefilled)) continue;
    const value = values[key];
    if (typeof value === 'string' && value.trim() !== '') body[key] = value.trim();
  }
  if (clear.length) body.clear_fields = clear;
  return body;
}

export function integrationFor(key: string): Integration | undefined {
  return INTEGRATIONS.find((i) => i.fields.some((f) => f.key === key));
}
