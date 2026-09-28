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

`accentAlt`, `progressTrack`, `ring2`, `ring3`, `glow` (or `null`), `fontFamily`, `numberFontFamily`, `showCornerCut`, `motif` (`none` | `petal` | `bloom` | `leaf` | `hud` | `grid`).

## Generated assets (`assets`)

A pack **cannot carry files**. It round-trips through `localStorage`, a `Blob` export
(`${packId}.pack.json`) and a `GET/PUT /api/users/me/theme` JSON column, and the import
control only accepts `application/json`. So an asset has to be a **string**: a
`url("data:image/svg+xml,…")` value or a public-dir path.

`assets` is a **non-schema key** sitting beside `tokens` — `validateThemePackage` only knows
the `HealthThemeTokens` list, so it is validated only by the code that reads it. That is
deliberate: the server hard-validates `kind` and `schemaVersion` only, so a new asset field
rides through `PUT /api/users/me/theme` untouched.

Three rules for a generated motif tile, or it will look broken:

1. **Square, with `width` == `height` == the viewBox extent.** CSS repeats the tile on a
   square lattice, so anything else seams at every edge.
2. **Colours as `var(--site-accent)` / `var(--site-accent-alt)`**, not baked hex. One tile
   then works on every theme in the pack instead of being right on exactly one.
3. **No `<text>` and no `font-family`.** The UI ships Google Fonts only, and the Android
   widget gets colours alone, so a glyph falls back to whatever the render host has.

See `alpaca/scripts/install_theme_pack.py`, which turns a benchmark answer into a pack, and
`alpaca/docs/BENCHMARKS.md` for the grader that checks all three.

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
