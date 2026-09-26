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

  it('scopes themes by surface without leaking android-only ones into the site picker', () => {
    // Synthetic pack: the shipped themes are Jarvis-wide, but the surface
    // filter must still work for imported packs that opt into a scope.
    const pack = createThemePack('scoped-pack', 'Scoped Pack');
    pack.themes = [
      {
        ...createThemePackage('widget-only', 'Widget Only', {
          bg: '#111111',
          text: '#EEEEEE',
          accent: '#22D3EE',
        }),
        scope: 'android_widget' as const,
      },
      createThemePackage('everywhere', 'Everywhere', {
        bg: '#101010',
        text: '#FAFAFA',
        accent: '#A78BFA',
      }),
    ];
    registry.importPackJson(serializeThemePack(pack));

    const site = registry.listThemesForSurface('site').map((t) => t.id);
    const android = registry.listThemesForSurface('android_widget').map((t) => t.id);

    expect(site).toContain('everywhere');
    expect(site).not.toContain('widget-only');
    expect(android).toContain('widget-only');
    expect(android).toContain('everywhere');
  });

  it('keeps every shipped theme available Jarvis-wide', () => {
    const site = registry.listThemesForSurface('site').map((t) => t.id);
    for (const id of ['aurora', 'bloom', 'iron', 'tron', 'neon', 'clean-athletic']) {
      expect(site).toContain(id);
    }
  });

  it('ships an icon with every default theme', () => {
    for (const theme of registry.listAllThemes()) {
      expect(theme.icon, `${theme.id} is missing an icon`).toBeTruthy();
    }
  });

  it('bloom keeps the Jarvis-wide base with floral character', () => {
    const bloom = registry.getTheme('bloom')!;
    // same structure as the Jarvis default, but floral palette + motif
    expect(bloom.tokens.bg).toBe(registry.getTheme('aurora')!.tokens.bg);
    expect(bloom.tokens.motif).toBe('petal');
    // Jarvis-wide: no surface restriction
    expect(bloom.scope ?? 'both').toBe('both');
  });

  it('resolves default theme and falls back when missing', () => {
    expect(registry.resolveTheme(DEFAULT_HEALTH_THEME_ID).id).toBe('aurora');
    expect(registry.resolveTheme('does-not-exist').id).toBe('aurora');
    expect(registry.resolveTheme(null).id).toBe('aurora');
  });

  it('exposes CSS vars from tokens (not hardcoded in widget)', () => {
    const vars = registry.cssVarsFor('bloom');
    expect(vars['--ht-bg']).toBe('#0F172A');
    expect(vars['--ht-accent']).toBe('#E06A9A');
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
