# Themes (Jarvis-wide)

Jarvis has **one active theme** at a time. Themes are not per-widget and not
per-page: the website chrome, every dashboard widget, and the native Android
health widget all follow the same theme.

## Where it lives

| Piece | Path |
|---|---|
| Token schema + validation | `services/ui/src/themes/types.ts` |
| Pack registry (import/export/disable) | `services/ui/src/themes/registry.ts` |
| Site application + active theme | `services/ui/src/themes/siteTheme.ts` |
| Pack manager UI (Settings) | `services/ui/src/themes/ThemePackageManager.tsx` |
| Settings section | `services/ui/src/components/settings/SiteThemePanel.tsx` |
| Shipped pack (data, not code) | `services/ui/src/themes/packs/jarvis-default.pack.json` |
| Per-user persistence | `GET/PUT /api/users/me/theme` (Identity, `UserThemeSetting`) |

## Pack schematic (schemaVersion 1)

```json
{
  "schemaVersion": 1,
  "kind": "jarvis.health-theme-pack",
  "id": "my-pack",
  "name": "My Pack",
  "themes": [
    {
      "id": "aurora",
      "name": "Aurora",
      "version": "1.0.0",
      "icon": "🌌",
      "tokens": {
        "bg": "#0F172A", "surface": "#1E293B", "text": "#F1F5F9",
        "textMuted": "#94A3B8", "border": "#334155", "accent": "#863BFF",
        "onAccent": "#FFFFFF", "progress": "#863BFF", "ring": "#863BFF",
        "radius": 18
      }
    }
  ]
}
```

Required tokens: `bg, surface, text, textMuted, border, accent, onAccent,
progress, ring, radius`. Optional: `accentAlt, progressTrack, ring2, ring3,
glow, fontFamily, numberFontFamily, showCornerCut, motif (none|petal|hud|grid)`,
plus the theme-level `icon` (short emoji/glyph) and `scope` (see below).

Validation lives in `validateThemePack` and runs on import, on server packs,
and on the shipped default pack (covered by `healthTheme.test.ts`).

## How a theme reaches the UI

1. `applySiteTheme(id)` writes `--site-*` and `--ht-*` CSS variables onto
   `<html>` and sets `data-theme-id` / `data-site-theme-active`.
2. `index.css` uses those variables for panels, cards, inputs, buttons, the
   body background (including an optional tiled motif pattern), plus a
   `[data-site-theme-active]` block that remaps the most widely used utility
   colours so a theme visibly restyles the whole interface.
3. `useActiveThemeId()` (`useSyncExternalStore` over the registry) is how
   widgets read the active theme — this is the only supported way for a widget
   to be themed.

## Scopes

`scope` may be `site`, `android_widget`, or `both` (default). The surface is
selected with `ThemeRegistry.listThemesForSurface()`. The shipped themes are
all `both`; the filter exists so imported packs can restrict a theme.

## Android widget tinting

The native home-screen widget cannot read the pack JSON. Instead the web
Health widget publishes the resolved `themeAccent` / `themeAccentText` into its
widget config (`HealthActivityWidget`), and `HealthWidget.java` reads those
from `/api/widgets/settings`. Palettes therefore stay in the pack data and are
never hardcoded in Java.

## Importing and editing

Settings → **Website theme** → Theme package manager:

- import a pack from a file or pasted JSON
- export any installed pack
- create an empty pack from the schematic, clone a built-in theme to edit it
- edit tokens, enable/disable individual themes, remove user packs
- built-in packs are read-only (clone first)

User packs are stored locally and can be synced to the account with
**Sync packs to account** (persisted per user by Identity).

## Tests

- `src/themes/healthTheme.test.ts` — schematic, registry, scopes, icons
- `src/themes/siteTheme.test.ts` — CSS var mapping, server load/save, offline
- `src/themes/ThemePackageManager.test.tsx` — import/export/edit/disable/remove
- `services/identity/tests/test_user_theme.py` — per-user endpoints
