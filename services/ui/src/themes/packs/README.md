# Health theme packs

Theme packs are portable JSON documents. Widgets **never** hardcode palettes — they resolve themes through `themeRegistry`.

## Schematic (`schemaVersion: 1`)

```json
{
  "schemaVersion": 1,
  "kind": "jarvis.health-theme-pack",
  "id": "my-pack",
  "name": "My Pack",
  "description": "Optional",
  "version": "1.0.0",
  "builtin": false,
  "themes": [
    {
      "schemaVersion": 1,
      "id": "my-theme",
      "name": "My Theme",
      "version": "1.0.0",
      "tokens": {
        "bg": "#0F172A",
        "surface": "#1E293B",
        "text": "#F1F5F9",
        "textMuted": "#94A3B8",
        "border": "#334755",
        "accent": "#863BFF",
        "onAccent": "#F8FAFC",
        "progress": "#863BFF",
        "ring": "#863BFF",
        "radius": 16,
        "motif": "none"
      }
    }
  ]
}
```

### Required token colors

`bg`, `surface`, `text`, `textMuted`, `border`, `accent`, `onAccent`, `progress`, `ring` — hex `#RGB` / `#RRGGBB` / `#AARRGGBB`.

### Optional tokens

`accentAlt`, `progressTrack`, `ring2`, `ring3`, `glow` (or `null`), `fontFamily`, `numberFontFamily`, `showCornerCut`, `motif` (`none` | `petal` | `hud` | `grid`).

## Create / import / edit / remove

| Action | API |
|--------|-----|
| Create empty pack | `themeRegistry.createEmptyPack(id, name)` |
| Add theme | `createThemePackage(...)` then `saveUserPack` |
| Import JSON | `themeRegistry.importPackJson(json)` |
| Edit tokens | `cloneThemeToUserPack` → `updateThemeTokens` (builtin is read-only) |
| Disable theme | `themeRegistry.setThemeEnabled(id, false)` |
| Remove theme | `themeRegistry.removeTheme(packId, themeId)` (user packs) |
| Remove pack | `themeRegistry.removePack(packId)` (user packs only) |
| Export | `themeRegistry.exportPack(packId)` |

Validation helpers: `validateThemePack`, `validateThemePackage`, `parseThemePackJson`.

## Default pack

`jarvis-default.pack.json` ships the six stock themes (Aurora, Bloom, Iron, Tron, Neon, Clean Athletic). It is **data**, not component code — edit the JSON or import a replacement under a different pack id.
