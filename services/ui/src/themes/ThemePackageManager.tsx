import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, Plus, Trash2, Upload, Palette, Eye, EyeOff } from 'lucide-react';
import { themeRegistry } from './registry';
import {
  createThemePackage,
  validateThemePack,
  type ThemePack,
  type ThemePackage,
} from './types';

interface ThemePackageManagerProps {
  /** Called when user picks a theme for the widget */
  selectedThemeId?: string;
  onSelectTheme?: (themeId: string) => void;
  /** Compact mode for embedding inside widget settings */
  compact?: boolean;
  /** Which surface this manager is configuring (site or Android widget) */
  surface?: 'site' | 'android_widget';
}

function swatchStyle(tokens: ThemePackage['tokens']): React.CSSProperties {
  return {
    background: `linear-gradient(135deg, ${tokens.bg} 0%, ${tokens.surface} 55%, ${tokens.accent} 100%)`,
    borderColor: tokens.border,
  };
}

/**
 * UI for browsing, importing, editing, and removing health theme packages.
 * All palettes come from the registry — none are hardcoded here.
 */
export function ThemePackageManager({
  selectedThemeId,
  onSelectTheme,
  compact = false,
  surface = 'site',
}: ThemePackageManagerProps) {
  const [packs, setPacks] = useState<ThemePack[]>(() => themeRegistry.listPacks());
  const [themes, setThemes] = useState(() => themeRegistry.listAllThemesForSurface(surface));
  const [importError, setImportError] = useState<string | null>(null);
  const [importOk, setImportOk] = useState<string | null>(null);
  const [importText, setImportText] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [editing, setEditing] = useState<{
    packId: string;
    themeId: string;
    tokens: ThemePackage['tokens'];
  } | null>(null);

  const refresh = useCallback(() => {
    setPacks(themeRegistry.listPacks());
    setThemes(themeRegistry.listAllThemesForSurface(surface));
  }, [surface]);

  useEffect(() => themeRegistry.subscribe(refresh), [refresh]);

  const selected = useMemo(
    () => selectedThemeId ?? themes[0]?.id ?? 'aurora',
    [selectedThemeId, themes]
  );

  const handleImport = () => {
    setImportError(null);
    setImportOk(null);
    try {
      const pack = themeRegistry.importPackJson(importText);
      setImportOk(`Imported pack “${pack.name}” (${pack.themes.length} themes).`);
      setImportText('');
      setShowImport(false);
      refresh();
    } catch (e) {
      setImportError((e as Error).message);
    }
  };

  const handleExport = (packId: string) => {
    try {
      const json = themeRegistry.exportPack(packId);
      const blob = new Blob([json], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${packId}.pack.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setImportError((e as Error).message);
    }
  };

  const handleCreatePack = () => {
    const id = window.prompt('New pack id (slug, e.g. my-themes)');
    if (!id) return;
    const name = window.prompt('Pack display name', id) || id;
    try {
      const pack = themeRegistry.createEmptyPack(id.toLowerCase(), name);
      // Seed one schematic theme so the pack is immediately usable
      pack.themes = [
        createThemePackage(`${pack.id}-1`, 'New Theme', {
          bg: '#0F172A',
          text: '#F1F5F9',
          accent: '#863BFF',
          surface: '#1E293B',
        }),
      ];
      themeRegistry.saveUserPack(pack);
      refresh();
    } catch (e) {
      setImportError((e as Error).message);
    }
  };

  const handleClone = (themeId: string) => {
    const packId = window.prompt(
      'Clone into pack id (existing user pack, or new id)',
      `${themeId}-custom`
    );
    if (!packId) return;
    try {
      themeRegistry.cloneThemeToUserPack(themeId, packId.toLowerCase(), {
        newThemeId: `${themeId}-custom`,
        newName: `${themeId} custom`,
      });
      refresh();
      setImportOk(`Cloned “${themeId}” into pack “${packId}” (now editable).`);
    } catch (e) {
      setImportError((e as Error).message);
    }
  };

  const handleSaveEdit = () => {
    if (!editing) return;
    try {
      themeRegistry.updateThemeTokens(editing.packId, editing.themeId, editing.tokens);
      setEditing(null);
      setImportOk('Theme saved.');
      refresh();
    } catch (e) {
      setImportError((e as Error).message);
    }
  };

  const handleFileImport = async (file: File) => {
    const text = await file.text();
    setImportText(text);
    setImportError(null);
    setImportOk(null);
    try {
      // Validate first for clearer errors
      JSON.parse(text);
      const pack = themeRegistry.importPackJson(text);
      setImportOk(`Imported pack “${pack.name}”.`);
      setImportText('');
      refresh();
    } catch (e) {
      setImportError((e as Error).message);
      setShowImport(true);
    }
  };

  return (
    <div
      className="space-y-3 text-sm"
      data-testid="theme-package-manager"
      style={{ color: 'var(--ht-text, #F1F5F9)' }}
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 font-semibold">
          <Palette size={16} aria-hidden />
          Theme packages
        </div>
        <div className="flex gap-2 flex-wrap">
          <button
            type="button"
            className="glass-button px-2 py-1 text-xs"
            onClick={handleCreatePack}
          >
            <Plus size={12} className="inline mr-1" aria-hidden />
            New pack
          </button>
          <button
            type="button"
            className="glass-button px-2 py-1 text-xs"
            onClick={() => setShowImport((v) => !v)}
          >
            <Upload size={12} className="inline mr-1" aria-hidden />
            Import
          </button>
        </div>
      </div>

      {importOk && (
        <p className="text-xs text-emerald-400" role="status">
          {importOk}
        </p>
      )}
      {importError && (
        <p className="text-xs text-red-400 whitespace-pre-wrap" role="alert">
          {importError}
        </p>
      )}

      {showImport && (
        <div className="rounded-lg border border-white/10 p-2 space-y-2">
          <p className="text-xs opacity-70">
            Paste a pack JSON ({'{ kind: "jarvis.health-theme-pack", … }'}) or choose a file.
          </p>
          <input
            type="file"
            accept="application/json,.json"
            className="block w-full text-xs"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void handleFileImport(f);
            }}
          />
          <textarea
            className="w-full h-28 rounded bg-black/30 border border-white/10 p-2 font-mono text-xs"
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder='{"schemaVersion":1,"kind":"jarvis.health-theme-pack",...}'
            spellCheck={false}
          />
          <div className="flex gap-2">
            <button type="button" className="glass-button px-2 py-1 text-xs" onClick={handleImport}>
              Install pack
            </button>
            <button
              type="button"
              className="glass-button px-2 py-1 text-xs"
              onClick={() => {
                const demo = themeRegistry.exportPack('jarvis-default');
                setImportText(demo);
              }}
            >
              Load example
            </button>
          </div>
        </div>
      )}

      {!compact && (
        <div className="space-y-2">
          {packs.map((pack) => (
            <div key={pack.id} className="rounded-xl border border-white/10 p-3">
              <div className="flex items-start justify-between gap-2 mb-2">
                <div>
                  <div className="font-medium text-sm">
                    {pack.name}
                    {pack.builtin && (
                      <span className="ml-2 text-[10px] uppercase tracking-wide opacity-50">
                        builtin
                      </span>
                    )}
                  </div>
                  <div className="text-xs opacity-60">
                    {pack.id} · v{pack.version} · {pack.themes.length} themes
                  </div>
                  {pack.description && (
                    <div className="text-xs opacity-50 mt-0.5">{pack.description}</div>
                  )}
                </div>
                <div className="flex gap-1">
                  <button
                    type="button"
                    className="glass-button px-2 py-1 text-xs"
                    onClick={() => handleExport(pack.id)}
                    title="Export pack JSON"
                  >
                    <Download size={12} aria-hidden />
                  </button>
                  {!pack.builtin && (
                    <button
                      type="button"
                      className="glass-button px-2 py-1 text-xs text-red-300"
                      onClick={() => {
                        if (window.confirm(`Remove pack “${pack.name}”?`)) {
                          try {
                            themeRegistry.removePack(pack.id);
                            refresh();
                          } catch (e) {
                            setImportError((e as Error).message);
                          }
                        }
                      }}
                      title="Remove pack"
                    >
                      <Trash2 size={12} aria-hidden />
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className={compact ? 'space-y-2' : 'grid grid-cols-2 sm:grid-cols-3 gap-2'}>
        {themes.map((t) => {
          const active = t.id === selected;
          const enabled = themeRegistry.isThemeEnabled(t.id);
          return (
            <div
              key={`${t.packId}:${t.id}`}
              className={`rounded-xl border p-2 transition ${active ? 'ring-2' : ''}`}
              style={{
                ...swatchStyle(t.tokens),
                // @ts-expect-error CSS var from theme
                '--tw-ring-color': t.tokens.accent,
                opacity: enabled ? 1 : 0.45,
              }}
            >
              <button
                type="button"
                className="w-full text-left"
                onClick={() => onSelectTheme?.(t.id)}
                disabled={!enabled}
              >
                <div
                  className="h-10 rounded-lg mb-2 border"
                  style={{
                    background: t.tokens.bg,
                    borderColor: t.tokens.border,
                    boxShadow: t.tokens.glow
                      ? `0 0 12px ${t.tokens.glow}55`
                      : undefined,
                  }}
                >
                  <div
                    className="h-1.5 rounded-full m-2"
                    style={{ background: t.tokens.progress, width: '60%' }}
                  />
                </div>
                <div
                  className="flex items-center gap-1.5"
                  style={{ color: t.tokens.text }}
                >
                  {t.icon && (
                    <span aria-hidden className="text-sm leading-none">
                      {t.icon}
                    </span>
                  )}
                  <span className="text-xs font-semibold">{t.name}</span>
                </div>
                <div className="text-[10px]" style={{ color: t.tokens.textMuted }}>
                  {t.packName}
                  {t.builtin ? '' : ' · user'}
                </div>
              </button>
              <div className="flex gap-1 mt-1">
                <button
                  type="button"
                  className="glass-button px-1.5 py-0.5 text-[10px]"
                  onClick={() => themeRegistry.setThemeEnabled(t.id, !enabled)}
                  title={enabled ? 'Disable theme' : 'Enable theme'}
                >
                  {enabled ? <Eye size={10} aria-hidden /> : <EyeOff size={10} aria-hidden />}
                </button>
                <button
                  type="button"
                  className="glass-button px-1.5 py-0.5 text-[10px]"
                  onClick={() => handleClone(t.id)}
                  title="Clone to editable pack"
                >
                  <Plus size={10} aria-hidden />
                </button>
                {!t.builtin && (
                  <button
                    type="button"
                    className="glass-button px-1.5 py-0.5 text-[10px]"
                    onClick={() =>
                      setEditing({ packId: t.packId, themeId: t.id, tokens: { ...t.tokens } })
                    }
                    title="Edit tokens"
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {editing && (
        <div className="rounded-xl border border-white/15 p-3 space-y-2 bg-black/20">
          <div className="font-medium text-sm">
            Edit {editing.themeId} <span className="opacity-50">({editing.packId})</span>
          </div>
          {(
            [
              'bg',
              'surface',
              'text',
              'textMuted',
              'border',
              'accent',
              'onAccent',
              'progress',
              'ring',
              'accentAlt',
              'glow',
            ] as const
          ).map((key) => (
            <label key={key} className="flex items-center gap-2 text-xs">
              <span className="w-28 opacity-70">{key}</span>
              <input
                type="color"
                className="w-8 h-6 rounded border-0 bg-transparent"
                value={
                  typeof editing.tokens[key] === 'string' &&
                  /^#[0-9A-Fa-f]{6}$/.test(editing.tokens[key] as string)
                    ? (editing.tokens[key] as string)
                    : '#000000'
                }
                onChange={(e) =>
                  setEditing((prev) =>
                    prev
                      ? { ...prev, tokens: { ...prev.tokens, [key]: e.target.value } }
                      : prev
                  )
                }
              />
              <input
                type="text"
                className="flex-1 rounded bg-black/30 border border-white/10 px-1.5 py-0.5 font-mono"
                value={String(editing.tokens[key] ?? '')}
                onChange={(e) =>
                  setEditing((prev) =>
                    prev
                      ? { ...prev, tokens: { ...prev.tokens, [key]: e.target.value || undefined } }
                      : prev
                  )
                }
              />
            </label>
          ))}
          <label className="flex items-center gap-2 text-xs">
            <span className="w-28 opacity-70">radius</span>
            <input
              type="number"
              min={0}
              max={40}
              className="w-20 rounded bg-black/30 border border-white/10 px-1.5"
              value={editing.tokens.radius}
              onChange={(e) =>
                setEditing((prev) =>
                  prev
                    ? { ...prev, tokens: { ...prev.tokens, radius: Number(e.target.value) || 0 } }
                    : prev
                )
              }
            />
          </label>
          <div className="flex gap-2">
            <button type="button" className="glass-button px-2 py-1 text-xs" onClick={handleSaveEdit}>
              Save theme
            </button>
            <button
              type="button"
              className="glass-button px-2 py-1 text-xs"
              onClick={() => setEditing(null)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="glass-button px-2 py-1 text-xs"
              onClick={() => {
                try {
                  const draft = {
                    ...themeRegistry.getPack(editing.packId)!,
                    themes: [
                      {
                        schemaVersion: 1 as const,
                        id: editing.themeId,
                        name: editing.themeId,
                        version: '1.0.0',
                        tokens: editing.tokens,
                      },
                    ],
                  };
                  const v = validateThemePack(draft);
                  setImportError(
                    v.ok ? null : `Schematic errors:\n- ${v.errors.filter((e) => !e.startsWith('duplicate')).join('\n- ')}`
                  );
                  if (v.ok) setImportOk('Tokens pass schematic validation.');
                } catch (e) {
                  setImportError((e as Error).message);
                }
              }}
            >
              Validate
            </button>
          </div>
        </div>
      )}

      {!compact && (
        <p className="text-[11px] opacity-50 leading-relaxed">
          Packs are JSON ({'{ kind: "jarvis.health-theme-pack" }'}). Builtin packs are read-only —
          clone a theme into a user pack to edit. Export shares the full pack file.
        </p>
      )}
    </div>
  );
}

export default ThemePackageManager;
