import {
  DEFAULT_HEALTH_THEME_ID,
  THEME_PACK_SCHEMA_VERSION,
  createThemePack,
  parseThemePackJson,
  serializeThemePack,
  themeToCssVars,
  validateThemePack,
  type HealthThemeCssVars,
  type ThemePack,
  type ThemePackage,
} from './types';
import defaultPackJson from './packs/jarvis-default.pack.json';

const STORAGE_KEY = 'jarvis_health_theme_packs_v1';
const DISABLED_KEY = 'jarvis_health_disabled_themes_v1';

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // quota / private mode — registry still works in-memory for the session
  }
}

/**
 * Runtime registry of theme packs.
 * - Builtin packs load from JSON assets (not hardcoded in widgets).
 * - User packs persist in storage and can be imported, edited, removed.
 * - Individual themes can be disabled without deleting the pack.
 */
export class ThemeRegistry {
  private packs: ThemePack[] = [];
  private disabledThemeIds = new Set<string>();
  private listeners = new Set<() => void>();

  constructor() {
    this.reload();
  }

  /** Re-read storage + re-seed builtin pack (does not drop user packs). */
  reload(): void {
    const builtin = clone(defaultPackJson) as ThemePack;
    builtin.builtin = true;

    const storedPacks = readJson<ThemePack[]>(STORAGE_KEY, []);
    const userPacks: ThemePack[] = [];
    for (const p of storedPacks) {
      const result = validateThemePack(p);
      if (result.ok && p.id !== builtin.id) {
        userPacks.push(p);
      }
    }

    this.packs = [builtin, ...userPacks];
    const disabled = readJson<string[]>(DISABLED_KEY, []);
    this.disabledThemeIds = new Set(Array.isArray(disabled) ? disabled : []);
    this.emit();
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(): void {
    for (const l of this.listeners) l();
  }

  private persistUserPacks(): void {
    const userPacks = this.packs.filter((p) => !p.builtin && p.id !== 'jarvis-default');
    writeJson(STORAGE_KEY, userPacks);
  }

  private persistDisabled(): void {
    writeJson(DISABLED_KEY, [...this.disabledThemeIds]);
  }

  listPacks(): ThemePack[] {
    return this.packs.map(clone);
  }

  getPack(packId: string): ThemePack | undefined {
    const found = this.packs.find((p) => p.id === packId);
    return found ? clone(found) : undefined;
  }

  /** All enabled themes across packs (for pickers). */
  listThemes(): Array<ThemePackage & { packId: string; packName: string; builtin: boolean }> {
    return this.listAllThemes().filter((t) => !this.disabledThemeIds.has(t.id));
  }

  /**
   * Themes usable in a given surface. `android_widget` themes are deliberately
   * hidden from the website picker and vice versa.
   */
  listThemesForSurface(
    surface: 'site' | 'android_widget'
  ): Array<ThemePackage & { packId: string; packName: string; builtin: boolean }> {
    return this.listThemes().filter((t) => {
      const scope = t.scope ?? 'both';
      return scope === 'both' || scope === surface;
    });
  }

  /**
   * Management view: same surface filter but keeps disabled themes visible so
   * they can be re-enabled (a picker must not show them, the manager must).
   */
  listAllThemesForSurface(
    surface: 'site' | 'android_widget'
  ): Array<ThemePackage & { packId: string; packName: string; builtin: boolean }> {
    return this.listAllThemes().filter((t) => {
      const scope = t.scope ?? 'both';
      return scope === 'both' || scope === surface;
    });
  }

  /** All themes including disabled (for management UI). */
  listAllThemes(): Array<ThemePackage & { packId: string; packName: string; builtin: boolean }> {
    const out: Array<ThemePackage & { packId: string; packName: string; builtin: boolean }> = [];
    for (const pack of this.packs) {
      for (const theme of pack.themes) {
        out.push({
          ...clone(theme),
          packId: pack.id,
          packName: pack.name,
          builtin: pack.builtin === true,
        });
      }
    }
    return out;
  }

  /** Resolve a theme by id (searches disabled too so widgets don't crash). */
  getTheme(themeId: string): ThemePackage | undefined {
    for (const pack of this.packs) {
      const t = pack.themes.find((x) => x.id === themeId);
      if (t) return clone(t);
    }
    return undefined;
  }

  /** Resolve theme or fall back to default pack's first theme. */
  resolveTheme(themeId: string | null | undefined): ThemePackage {
    const id = themeId && themeId.trim() !== '' ? themeId : DEFAULT_HEALTH_THEME_ID;
    const found = this.getTheme(id);
    if (found) return found;
    const fallback =
      this.getTheme(DEFAULT_HEALTH_THEME_ID) ??
      this.packs[0]?.themes[0];
    if (!fallback) {
      // Absolute last-resort minimal theme so the UI never blanks.
      return {
        schemaVersion: THEME_PACK_SCHEMA_VERSION,
        id: 'emergency',
        name: 'Emergency',
        version: '1.0.0',
        tokens: {
          bg: '#0F172A',
          surface: '#1E293B',
          text: '#F1F5F9',
          textMuted: '#94A3B8',
          border: '#334755',
          accent: '#863BFF',
          onAccent: '#F8FAFC',
          progress: '#863BFF',
          ring: '#863BFF',
          radius: 16,
        },
      };
    }
    return clone(fallback);
  }

  cssVarsFor(themeId: string | null | undefined): HealthThemeCssVars {
    return themeToCssVars(this.resolveTheme(themeId).tokens);
  }

  isThemeEnabled(themeId: string): boolean {
    return !this.disabledThemeIds.has(themeId);
  }

  setThemeEnabled(themeId: string, enabled: boolean): void {
    if (enabled) this.disabledThemeIds.delete(themeId);
    else this.disabledThemeIds.add(themeId);
    this.persistDisabled();
    this.emit();
  }

  /**
   * Import a pack from JSON. Replaces an existing pack with the same id
   * (except refuses to overwrite builtin jarvis-default unless force).
   */
  importPackJson(json: string, opts?: { force?: boolean }): ThemePack {
    const pack = parseThemePackJson(json);
    const existingIdx = this.packs.findIndex((p) => p.id === pack.id);
    if (existingIdx >= 0) {
      const existing = this.packs[existingIdx];
      if (existing.builtin && !opts?.force) {
        throw new Error(
          `Pack "${pack.id}" is built-in. Import under a new id or pass force to replace.`
        );
      }
      this.packs.splice(existingIdx, 1, pack);
    } else {
      this.packBuiltinGuard(pack);
      this.packs.push(pack);
    }
    if (!existingIdx || existingIdx < 0) this.persistUserPacks();
    else this.persistUserPacks();
    this.emit();
    return clone(pack);
  }

  private packBuiltinGuard(pack: ThemePack): void {
    if (pack.builtin) {
      // Imported packs are never treated as builtin after install.
      pack.builtin = false;
    }
  }

  /** Create or replace a user pack (must not claim builtin). */
  saveUserPack(pack: ThemePack): ThemePack {
    const result = validateThemePack(pack);
    if (!result.ok) {
      throw new Error(`Invalid pack:\n- ${result.errors.join('\n- ')}`);
    }
    if (pack.id === 'jarvis-default') {
      throw new Error('Cannot modify the builtin jarvis-default pack id');
    }
    const normalized = { ...clone(pack), builtin: false };
    const idx = this.packs.findIndex((p) => p.id === normalized.id);
    if (idx >= 0) {
      if (this.packs[idx].builtin) {
        throw new Error(`Pack "${normalized.id}" is built-in and cannot be replaced`);
      }
      this.packs.splice(idx, 1, normalized);
    } else {
      this.packs.push(normalized);
    }
    this.persistUserPacks();
    this.emit();
    return clone(normalized);
  }

  /** Remove a user pack by id. Builtin packs cannot be removed. */
  removePack(packId: string): void {
    const pack = this.packs.find((p) => p.id === packId);
    if (!pack) throw new Error(`Pack "${packId}" not found`);
    if (pack.builtin || pack.id === 'jarvis-default') {
      throw new Error('Built-in packs cannot be removed (disable themes instead)');
    }
    this.packs = this.packs.filter((p) => p.id !== packId);
    this.persistUserPacks();
    this.emit();
  }

  /** Export a pack as portable JSON. */
  exportPack(packId: string): string {
    const pack = this.getPack(packId);
    if (!pack) throw new Error(`Pack "${packId}" not found`);
    return serializeThemePack(pack);
  }

  /**
   * Edit a theme's tokens (user packs only, or clone builtin into user pack first).
   * Returns the updated pack.
   */
  updateThemeTokens(
    packId: string,
    themeId: string,
    tokens: ThemePackage['tokens']
  ): ThemePack {
    const pack = this.packs.find((p) => p.id === packId);
    if (!pack) throw new Error(`Pack "${packId}" not found`);
    if (pack.builtin) {
      throw new Error('Built-in packs are read-only. Clone to a user pack first.');
    }
    const idx = pack.themes.findIndex((t) => t.id === themeId);
    if (idx < 0) throw new Error(`Theme "${themeId}" not found in pack "${packId}"`);
    pack.themes[idx] = { ...pack.themes[idx], tokens: clone(tokens) };
    this.persistUserPacks();
    this.emit();
    return clone(pack);
  }

  /** Remove one theme from a user pack. */
  removeTheme(packId: string, themeId: string): void {
    const pack = this.packs.find((p) => p.id === packId);
    if (!pack) throw new Error(`Pack "${packId}" not found`);
    if (pack.builtin) {
      throw new Error('Built-in packs are read-only. Disable the theme instead.');
    }
    const before = pack.themes.length;
    pack.themes = pack.themes.filter((t) => t.id !== themeId);
    if (pack.themes.length === before) {
      throw new Error(`Theme "${themeId}" not found in pack "${packId}"`);
    }
    this.persistUserPacks();
    this.emit();
  }

  /**
   * Clone a theme (from any pack) into a new or existing user pack so it
   * becomes editable. Creates an empty user pack when packId is new.
   */
  cloneThemeToUserPack(
    themeId: string,
    targetPackId: string,
    opts?: { newThemeId?: string; newName?: string }
  ): ThemePackage {
    const source = this.getTheme(themeId);
    if (!source) throw new Error(`Theme "${themeId}" not found`);

    let pack = this.packs.find((p) => p.id === targetPackId);
    if (pack?.builtin) {
      throw new Error('Cannot clone into a built-in pack');
    }
    if (!pack) {
      pack = createThemePack(targetPackId, targetPackId.replace(/-/g, ' '), {
        description: 'User theme pack',
      });
      this.packs.push(pack);
    }

    const newId = opts?.newThemeId ?? `${source.id}-copy`;
    if (pack.themes.some((t) => t.id === newId)) {
      throw new Error(`Theme id "${newId}" already exists in pack "${targetPackId}"`);
    }
    const cloneTheme: ThemePackage = {
      ...clone(source),
      id: newId,
      name: opts?.newName ?? `${source.name} (copy)`,
    };
    pack.themes.push(cloneTheme);
    this.persistUserPacks();
    this.emit();
    return clone(cloneTheme);
  }

  /** Start an empty user pack from the schematic. */
  createEmptyPack(id: string, name: string): ThemePack {
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(id)) {
      throw new Error('Pack id must be a slug (a-z, 0-9, -, _)');
    }
    if (this.packs.some((p) => p.id === id)) {
      throw new Error(`Pack "${id}" already exists`);
    }
    const pack = createThemePack(id, name, { description: 'User theme pack' });
    this.packs.push(pack);
    this.persistUserPacks();
    this.emit();
    return clone(pack);
  }
}

/** App-wide singleton registry. */
export const themeRegistry = new ThemeRegistry();
