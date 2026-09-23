import { describe, expect, it, beforeEach } from 'vitest';
import {
  DEFAULT_HEALTH_THEME_ID,
  createThemePackage,
  createThemePack,
  parseThemePackJson,
  serializeThemePack,
  validateThemePack,
} from './types';
import { ThemeRegistry } from './registry';
import defaultPack from './packs/jarvis-default.pack.json';

describe('theme pack schematic', () => {
  it('validates the shipped default pack', () => {
    const result = validateThemePack(defaultPack);
    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
  });

  it('rejects packs with wrong kind/schema', () => {
    const bad = { ...defaultPack, kind: 'nope', schemaVersion: 99 };
    const result = validateThemePack(bad);
    expect(result.ok).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
  });

  it('rejects duplicate theme ids', () => {
    const themes = defaultPack.themes;
    const dupe = {
      ...defaultPack,
      themes: [themes[0], { ...themes[1], id: themes[0].id }],
    };
    const result = validateThemePack(dupe);
    expect(result.ok).toBe(false);
    expect(result.errors.some((e) => e.includes('duplicate'))).toBe(true);
  });

  it('createThemePackage fills required token defaults', () => {
    const theme = createThemePackage('demo', 'Demo', {
      bg: '#111111',
      text: '#FFFFFF',
      accent: '#FF0000',
    });
    const pack = createThemePack('demo-pack', 'Demo Pack');
    pack.themes = [theme];
    expect(validateThemePack(pack).ok).toBe(true);
    expect(theme.tokens.ring).toBe('#FF0000');
    expect(theme.tokens.radius).toBe(16);
  });

  it('serialize → parse round-trips', () => {
    const json = serializeThemePack(defaultPack as never);
    const parsed = parseThemePackJson(json);
    expect(parsed.id).toBe('jarvis-default');
    expect(parsed.themes).toHaveLength(defaultPack.themes.length);
  });
});

describe('ThemeRegistry', () => {
  let registry: ThemeRegistry;

  beforeEach(() => {
    localStorage.clear();
    registry = new ThemeRegistry();
  });

  it('loads builtin pack with all default themes', () => {
    const packs = registry.listPacks();
    expect(packs[0].id).toBe('jarvis-default');
    expect(packs[0].builtin).toBe(true);
    const ids = registry.listThemes().map((t) => t.id);
    for (const id of ['aurora', 'bloom', 'iron', 'tron', 'neon', 'clean-athletic']) {
      expect(ids).toContain(id);
    }
  });

  it('resolves default theme and falls back when missing', () => {
    expect(registry.resolveTheme(DEFAULT_HEALTH_THEME_ID).id).toBe('aurora');
    expect(registry.resolveTheme('does-not-exist').id).toBe('aurora');
    expect(registry.resolveTheme(null).id).toBe('aurora');
  });

  it('exposes CSS vars from tokens (not hardcoded in widget)', () => {
    const vars = registry.cssVarsFor('bloom');
    expect(vars['--ht-bg']).toBe('#470A1D');
    expect(vars['--ht-accent']).toBe('#C36786');
  });

  it('imports a user pack and persists it', () => {
    const pack = createThemePack('custom-pack', 'Custom');
    pack.themes = [
      createThemePackage('custom-teal', 'Teal', {
        bg: '#042F2E',
        text: '#ECFEFF',
        accent: '#14B8A6',
      }),
    ];
    registry.importPackJson(serializeThemePack(pack));
    expect(registry.getPack('custom-pack')).toBeTruthy();

    const reloaded = new ThemeRegistry();
    expect(reloaded.getPack('custom-pack')).toBeTruthy();
    expect(reloaded.getTheme('custom-teal')?.name).toBe('Teal');
  });

  it('refuses to overwrite builtin pack without force', () => {
    const hijack = createThemePack('jarvis-default', 'Evil');
    hijack.themes = [
      createThemePackage('x', 'X', { bg: '#000', text: '#fff', accent: '#f00' }),
    ];
    expect(() => registry.importPackJson(serializeThemePack(hijack))).toThrow(/built-in/i);
  });

  it('removes user packs but not builtin', () => {
    const pack = createThemePack('temp', 'Temp');
    pack.themes = [
      createThemePackage('temp-theme', 'T', { bg: '#111', text: '#eee', accent: '#0f0' }),
    ];
    registry.importPackJson(serializeThemePack(pack));
    registry.removePack('temp');
    expect(registry.getPack('temp')).toBeUndefined();
    expect(() => registry.removePack('jarvis-default')).toThrow(/built-in/i);
  });

  it('disables and re-enables themes without deleting pack', () => {
    registry.setThemeEnabled('bloom', false);
    expect(registry.listThemes().map((t) => t.id)).not.toContain('bloom');
    registry.setThemeEnabled('bloom', true);
    expect(registry.listThemes().map((t) => t.id)).toContain('bloom');
  });

  it('clones builtin theme into editable user pack', () => {
    registry.cloneThemeToUserPack('tron', 'my-tron', { newThemeId: 'tron-custom' });
    const theme = registry.getTheme('tron-custom');
    expect(theme).toBeTruthy();
    // Source builtin still intact
    expect(registry.getTheme('tron')?.tokens.accent).toBe('#00E5FF');

    registry.updateThemeTokens('my-tron', 'tron-custom', {
      ...theme!.tokens,
      accent: '#FF00AA',
    });
    expect(registry.getTheme('tron-custom')?.tokens.accent).toBe('#FF00AA');
    // Builtin tron unchanged
    expect(registry.getTheme('tron')?.tokens.accent).toBe('#00E5FF');
  });

  it('blocks token edits on builtin packs', () => {
    expect(() =>
      registry.updateThemeTokens('jarvis-default', 'aurora', {
        ...registry.getTheme('aurora')!.tokens,
        accent: '#000000',
      })
    ).toThrow(/read-only|built-in/i);
  });

  it('exports pack JSON', () => {
    const json = registry.exportPack('jarvis-default');
    const parsed = parseThemePackJson(json);
    expect(parsed.id).toBe('jarvis-default');
  });
});
