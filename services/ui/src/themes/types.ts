/**
 * Health widget theme package schema (schemaVersion 1).
 *
 * Themes are data packages — never hardcoded in widget components.
 * A pack is a portable JSON document that can be imported, edited,
 * exported, enabled/disabled, or removed at runtime.
 */

export const THEME_PACK_SCHEMA_VERSION = 1 as const;
export const THEME_PACK_KIND = 'jarvis.health-theme-pack' as const;

/** Visual tokens every health theme must define. Colors are hex (#RRGGBB or #AARRGGBB). */
export interface HealthThemeTokens {
  /** Card / widget background */
  bg: string;
  /** Nested surface (tiles, chips) */
  surface: string;
  /** Primary text on bg */
  text: string;
  /** Secondary / label text */
  textMuted: string;
  /** Hairline borders */
  border: string;
  /** Primary accent (buttons, focus) */
  accent: string;
  /** Text/icon color that sits on accent fills */
  onAccent: string;
  /** Secondary accent (optional gradients, second ring) */
  accentAlt?: string;
  /** Progress bar / ring primary color */
  progress: string;
  /** Track behind progress (optional; defaults to border) */
  progressTrack?: string;
  /** Activity ring stroke */
  ring: string;
  /** Optional 2nd ring (e.g. exercise) */
  ring2?: string;
  /** Optional 3rd ring (e.g. stand / stairs) */
  ring3?: string;
  /** Glow color for neon themes; null/omit = no glow */
  glow?: string | null;
  /** Corner radius in dp/px for cards and tiles */
  radius: number;
  /** Optional CSS font-family for body */
  fontFamily?: string | null;
  /** Optional CSS font-family for big numbers */
  numberFontFamily?: string | null;
  /** Decorative corner cuts (Tron HUD) */
  showCornerCut?: boolean;
  /** Motif overlay id: none | petal | hud | grid */
  motif?: ThemeMotif;
}

export type ThemeMotif = 'none' | 'petal' | 'hud' | 'grid';

/** A single installable theme (one visual style). */
export interface ThemePackage {
  schemaVersion: typeof THEME_PACK_SCHEMA_VERSION;
  /** Stable slug id, unique within a pack and across installed packs */
  id: string;
  /** Display name */
  name: string;
  description?: string;
  /** Optional author credit */
  author?: string;
  /** Semver of this theme entry */
  version: string;
  tokens: HealthThemeTokens;
  /**
   * Where this theme may be used. `site` (default) appears in the website
   * picker; `android_widget` is only offered for the Android home-screen
   * fitness widget; `both` is available everywhere.
   */
  scope?: 'site' | 'android_widget' | 'both';
  /**
   * Theme icon. A single emoji/glyph keeps packs portable as plain JSON and
   * needs no image assets — e.g. "🌌" for Aurora, "🌸" for Bloom.
   */
  icon?: string;
}

/** A pack = named collection of themes that ships or imports as one unit. */
export interface ThemePack {
  schemaVersion: typeof THEME_PACK_SCHEMA_VERSION;
  /** Discriminator so importers can reject foreign JSON */
  kind: typeof THEME_PACK_KIND;
  /** Stable slug id, unique among installed packs */
  id: string;
  name: string;
  description?: string;
  version: string;
  /** Built-in packs cannot be deleted; their themes can still be disabled */
  builtin?: boolean;
  themes: ThemePackage[];
}

/** Result of validating a pack or theme against the schematic. */
export interface ThemeValidationResult {
  ok: boolean;
  errors: string[];
}

export const DEFAULT_HEALTH_THEME_ID = 'aurora';

/** CSS custom properties derived from tokens (applied on widget root). */
export type HealthThemeCssVars = Record<string, string>;

const HEX_RE = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;

function isHexColor(value: unknown): boolean {
  return typeof value === 'string' && HEX_RE.test(value.trim());
}

function requireString(
  errors: string[],
  obj: Record<string, unknown>,
  key: string,
  path: string
): void {
  const v = obj[key];
  if (typeof v !== 'string' || v.trim() === '') {
    errors.push(`${path}.${key} must be a non-empty string`);
  }
}

function requireHex(errors: string[], obj: Record<string, unknown>, key: string, path: string): void {
  if (!isHexColor(obj[key])) {
    errors.push(`${path}.${key} must be a hex color (#RGB, #RRGGBB, or #AARRGGBB)`);
  }
}

function optionalHex(errors: string[], obj: Record<string, unknown>, key: string, path: string): void {
  const v = obj[key];
  if (v !== undefined && v !== null && v !== '' && !isHexColor(v)) {
    errors.push(`${path}.${key} must be a hex color or null when set`);
  }
}

/** Validate a single theme package against schemaVersion 1. */
export function validateThemePackage(input: unknown, path = 'theme'): ThemeValidationResult {
  const errors: string[] = [];
  if (input === null || typeof input !== 'object') {
    return { ok: false, errors: [`${path} must be an object`] };
  }
  const t = input as Record<string, unknown>;
  if (t.schemaVersion !== THEME_PACK_SCHEMA_VERSION) {
    errors.push(`${path}.schemaVersion must be ${THEME_PACK_SCHEMA_VERSION}`);
  }
  requireString(errors, t, 'id', path);
  if (typeof t.id === 'string' && !/^[a-z0-9][a-z0-9_-]*$/.test(t.id)) {
    errors.push(`${path}.id must be a slug (a-z, 0-9, -, _)`);
  }
  requireString(errors, t, 'name', path);
  requireString(errors, t, 'version', path);
  if (t.scope !== undefined && !['site', 'android_widget', 'both'].includes(String(t.scope))) {
    errors.push(`${path}.scope must be one of site|android_widget|both`);
  }
  if (t.icon !== undefined && (typeof t.icon !== 'string' || t.icon.length > 8)) {
    errors.push(`${path}.icon must be a short string (emoji or glyph)`);
  }

  const tokens = t.tokens;
  if (tokens === null || typeof tokens !== 'object') {
    errors.push(`${path}.tokens must be an object`);
  } else {
    const tok = tokens as Record<string, unknown>;
    const pathT = `${path}.tokens`;
    for (const key of ['bg', 'surface', 'text', 'textMuted', 'border', 'accent', 'onAccent', 'progress', 'ring'] as const) {
      requireHex(errors, tok, key, pathT);
    }
    for (const key of ['accentAlt', 'progressTrack', 'ring2', 'ring3', 'glow'] as const) {
      optionalHex(errors, tok, key, pathT);
    }
    if (typeof tok.radius !== 'number' || !Number.isFinite(tok.radius) || tok.radius < 0) {
      errors.push(`${pathT}.radius must be a non-negative number`);
    }
    if (tok.motif !== undefined && !['none', 'petal', 'hud', 'grid'].includes(String(tok.motif))) {
      errors.push(`${pathT}.motif must be one of none|petal|hud|grid`);
    }
  }

  return { ok: errors.length === 0, errors };
}

/** Validate a full theme pack (schematic for import/create). */
export function validateThemePack(input: unknown): ThemeValidationResult {
  const errors: string[] = [];
  if (input === null || typeof input !== 'object') {
    return { ok: false, errors: ['pack must be an object'] };
  }
  const p = input as Record<string, unknown>;
  if (p.kind !== THEME_PACK_KIND) {
    errors.push(`kind must be "${THEME_PACK_KIND}"`);
  }
  if (p.schemaVersion !== THEME_PACK_SCHEMA_VERSION) {
    errors.push(`schemaVersion must be ${THEME_PACK_SCHEMA_VERSION}`);
  }
  requireString(errors, p, 'id', 'pack');
  if (typeof p.id === 'string' && !/^[a-z0-9][a-z0-9_-]*$/.test(p.id)) {
    errors.push('pack.id must be a slug (a-z, 0-9, -, _)');
  }
  requireString(errors, p, 'name', 'pack');
  requireString(errors, p, 'version', 'pack');

  const themes = p.themes;
  if (!Array.isArray(themes) || themes.length === 0) {
    errors.push('pack.themes must be a non-empty array');
  } else {
    const seen = new Set<string>();
    themes.forEach((theme, i) => {
      const result = validateThemePackage(theme, `pack.themes[${i}]`);
      errors.push(...result.errors);
      const id = (theme as { id?: unknown })?.id;
      if (typeof id === 'string') {
        if (seen.has(id)) errors.push(`duplicate theme id "${id}"`);
        seen.add(id);
      }
    });
  }

  return { ok: errors.length === 0, errors };
}

/**
 * Schematic: create a minimal valid theme from partial overrides.
 * Use this in docs/tests/UI "New theme" flows.
 */
export function createThemePackage(
  id: string,
  name: string,
  tokens: Partial<HealthThemeTokens> & Pick<HealthThemeTokens, 'bg' | 'accent' | 'text'>,
  extra?: Partial<Pick<ThemePackage, 'description' | 'author' | 'version'>>
): ThemePackage {
  const merged: HealthThemeTokens = {
    surface: tokens.surface ?? tokens.bg,
    textMuted: tokens.textMuted ?? '#94A3B8',
    border: tokens.border ?? '#33475569',
    onAccent: tokens.onAccent ?? '#0F172A',
    progress: tokens.progress ?? tokens.accent,
    ring: tokens.ring ?? tokens.accent,
    radius: tokens.radius ?? 16,
    ...tokens,
    bg: tokens.bg,
    text: tokens.text,
    accent: tokens.accent,
  };
  return {
    schemaVersion: THEME_PACK_SCHEMA_VERSION,
    id,
    name,
    version: extra?.version ?? '1.0.0',
    description: extra?.description,
    author: extra?.author,
    tokens: merged,
  };
}

/** Empty starter pack following the schematic. */
export function createThemePack(
  id: string,
  name: string,
  extra?: Partial<Pick<ThemePack, 'description' | 'version' | 'builtin'>>
): ThemePack {
  return {
    schemaVersion: THEME_PACK_SCHEMA_VERSION,
    kind: THEME_PACK_KIND,
    id,
    name,
    version: extra?.version ?? '1.0.0',
    description: extra?.description,
    builtin: extra?.builtin ?? false,
    themes: [],
  };
}

/** Flatten tokens → CSS custom properties for the widget root element. */
export function themeToCssVars(tokens: HealthThemeTokens): HealthThemeCssVars {
  const vars: HealthThemeCssVars = {
    '--ht-bg': tokens.bg,
    '--ht-surface': tokens.surface,
    '--ht-text': tokens.text,
    '--ht-text-muted': tokens.textMuted,
    '--ht-border': tokens.border,
    '--ht-accent': tokens.accent,
    '--ht-on-accent': tokens.onAccent,
    '--ht-progress': tokens.progress,
    '--ht-progress-track': tokens.progressTrack ?? tokens.border,
    '--ht-ring': tokens.ring,
    '--ht-radius': `${tokens.radius}px`,
    '--ht-motif': tokens.motif ?? 'none',
  };
  if (tokens.accentAlt) vars['--ht-accent-alt'] = tokens.accentAlt;
  if (tokens.ring2) vars['--ht-ring-2'] = tokens.ring2;
  if (tokens.ring3) vars['--ht-ring-3'] = tokens.ring3;
  if (tokens.glow) vars['--ht-glow'] = tokens.glow;
  if (tokens.fontFamily) vars['--ht-font'] = tokens.fontFamily;
  if (tokens.numberFontFamily) vars['--ht-number-font'] = tokens.numberFontFamily;
  if (tokens.showCornerCut) vars['--ht-corner-cut'] = '1';
  return vars;
}

/** Serialize a pack for export (stable key order for diffs). */
export function serializeThemePack(pack: ThemePack): string {
  return `${JSON.stringify(pack, null, 2)}\n`;
}

/** Parse + validate imported pack JSON. Throws with aggregated errors. */
export function parseThemePackJson(json: string): ThemePack {
  let raw: unknown;
  try {
    raw = JSON.parse(json);
  } catch (e) {
    throw new Error(`Invalid JSON: ${(e as Error).message}`, { cause: e });
  }
  const result = validateThemePack(raw);
  if (!result.ok) {
    throw new Error(`Theme pack failed schematic:\n- ${result.errors.join('\n- ')}`);
  }
  return raw as ThemePack;
}
