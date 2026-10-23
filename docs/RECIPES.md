# Recipes and meal planning

Status: **Phases 0–1 built** — the importer (`scripts/import_recipes.py`) renders the
first level-balanced note set; everything app-side (Phases 2+) is still planned.
Last live app commit `d766a19f` (v1.5.1).

Goal: fill the family's Recipes shelf with kid-tiered, fast and appliance-friendly meals,
then turn them into a weekly plan and a shared shopping list. Every artefact stays a plain
markdown note under `Recipes/` in Nextcloud, so the Recipes page, Notes search, chat sharing
and Jarvis memory all work with no new format.

## Ground rules

- Notes keep the standard header the app writes — `# Title`, `Category: Recipes`, blank
  line — with labels as inline `#tags`. Nextcloud Notes compatibility stays intact.
- Imported recipes carry: `#level-1`, `#level-2`, `#level-3`, `#slow-cooker`,
  `#instant-pot`, `#30-minutes`, `#5-ingredients`.
- All quantities are scaled to **7 servings** (family of 7; tweak per recipe in the note).
- Every imported recipe ends with a one-line source and licence note
  (Food.com — CC0, or Wikibooks Cookbook — CC BY-SA).

## Level rules (proposed)

| Level | Ages | Shape of the recipe | Source tags used |
| --- | --- | --- | --- |
| `#level-1` | 7–10 | No-cook / assembly / microwave, no sharp knives or heat | `no-cook`, `5-ingredients-or-less`, `easy` + short steps |
| `#level-2` | 11–14 | Stovetop and oven basics, simple knife work, ≤ 30 min | `easy`, `beginner-cook`, `30-minutes-or-less` |
| `#level-3` | 15–18 | Full meals: oven roasts, pressure cooker, multi-step | remainder, `crock-pot-slow-cooker`, pressure cooker titles |

Appliance tags: `crock-pot-slow-cooker` → `#slow-cooker`; "instant pot" / "pressure cooker"
in title or keywords → `#instant-pot`; `30-minutes-or-less` or total time ≤ 35 → `#30-minutes`;
`5-ingredients-or-less` → `#5-ingredients`.

## Staple list (proposed, used for overlap ranking)

chicken · ground beef or turkey · rice · pasta · canned tomatoes · beans · cheese ·
tortillas · eggs · potatoes · onions · garlic · frozen vegetables

## Food and nutrition data (decided 2026-10-22)

- **The Food.com corpus already carries per-recipe nutrition** (Calories, Fat,
  SaturatedFat, Cholesterol, Sodium, Carbohydrate, Fiber, Sugar, Protein, plus
  RecipeServings), so the import writes a per-serving nutrition line into each
  note, tagged `foodcom` — "as stated by the submitter", not lab ground truth.
  Confirm on first download whether those panels are per-serving or per-recipe
  and rescale to the note's 7 servings.
- **Computed nutrition is the fallback** (paste-a-link imports, edited recipes,
  future per-ingredient breakdowns): recipe-scrapers parses, ingredient-parser-NLP
  splits, an FTS match finds the ingredient in the health hub's USDA seed
  ([HEALTH_HUB.md](HEALTH_HUB.md) → Food data sources), FNDDS portion weights
  convert volume to grams (N = V × W / 100), and an override table keeps a fixed
  match fixed. The 2026 `recipe-macros-scraper` project runs this exact pipeline.
- TheMealDB stays a search/preview source (no nutrition; dev-key terms);
  schema.org `nutrition` from pasted links is best-effort only.

---

## Phase 0 — decisions

- [x] Importer home: `scripts/import_recipes.py`, a one-off workstation script
      (repo convention — `tools/` is reserved for execution-image subprocess tools).
- [x] Dataset access: `kagglehub`'s anonymous download works — no Kaggle account or
      token needed; `kagglehub.dataset_download("irkaal/foodcom-recipes-and-reviews")`
      (~723 MB) caches under `~/.cache/kagglehub/`, kept out of the repo. Nutrition
      columns are **per serving**: recipe #38's 170.9 kcal matches the live food.com
      JSON-LD byte for byte. The shuyangli94 fallback was not needed.
- [ ] Confirm the level rules, staple list and household serving count (7).
- [ ] Confirm the meal-plan note format below.

## Phase 1 — recipe importer

- [x] Download the dataset once via anonymous `kagglehub` (~723 MB, cached under
      `~/.cache/kagglehub/`); kept out of the repo.
- [x] Filter candidates in `scripts/import_recipes.py`. The corpus' real tag
      vocabulary is `kid friendly` / `easy` / `beginner cook` / `< 30 mins` /
      `< 60 mins` / `inexpensive` / `oven` / `stove top` (not the assumed
      `30-minutes-or-less` names). Level 1 tops that with heat/knife-word filters
      on the instructions; all levels drop dessert/drink/sauce noise, alcohol and
      recipes without numeric servings. Slow-cooker and pressure-cooker picks are
      detected from keywords/instructions as a bonus where they fit.
- [x] Rank by staple overlap, then fewer ingredients, shorter total time and
      better rating; a per-category cap keeps variety (~40 per level).
- [x] Render markdown from each recipe's live food.com JSON-LD (the dataset strips
      ingredient units): scaled `- [ ] 2 lb chicken` lines (×7/servings), numbered
      steps, source/licence line, and a per-serving nutrition block tagged
      `foodcom`.
- [x] Guarantee unique note titles (`Title.md`; suffix ` (2)` on collision).
- [ ] Wikibooks Cookbook (bonus): the `Category:Slow cooker recipes` set (~26 pages) through
      the MediaWiki API, converted to markdown with the CC BY-SA credit line.
- [x] Dry-run report first: funnel + sample notes printed before anything is
      written (522,517 rows → 339,044 with servings → 246,772 non-noise → 213,716
      ingredient/step bounds → 188,908 no alcohol; pools L1 2,253 / L2 56,171 /
      L3 88,426).

## Phase 2 — Recipes page touchpoints

- [x] **Share to chat** on recipe cards and in the editor — reuses the Bible
      reader's `ShareToChatSheet`, generalized with an optional title, icon and
      message builder. The chat message is a short `🍽 title: excerpt` with an
      optional intro note, never the whole file.
- [x] Family tab: Recipes is reachable today (Family → Create renders the
      page); a dedicated link can come with the meal-plan view.
- [x] "Teach Jarvis" RAG sync over `Recipes/` — a header button on the Recipes
      page calls the notes `sync_rag` route for the recipes folder.

## Phase 3 — weekly meal plan (shipped)

- [x] Format: `Recipes/Meal plan.md` — header + `#meal-plan` + one checklist line per day,
      e.g. `- [ ] Mon · Chicken Chili`. Ticking a day marks it cooked.
- [x] Plan view on the Recipes page (`Week plan`): 7 slots, pick a recipe per day from the
      shelf; **Save plan** writes the note like any other edit.
- [x] Shows each pick's level (from the note's `#level-N` tag); a quick "surprise me" fill
      and a warning when a recipe repeats in the week. Appliance chips
      (`#slow-cooker`/`#instant-pot`) still open.

## Phase 4 — shopping list (shipped)

- [x] Generated from the plan's recipes: the ingredients of each planned dinner are
      aggregated and duplicates are merged. Imported notes are already scaled to 7 servings.
- [x] Written to `Recipes/Shopping list.md` as a `- [ ]` checklist note, tappable like any note.
- [x] Regenerating keeps every ticked item (matched by item text); items ticked earlier that
      are no longer planned stay ticked at the bottom.
- [x] A **Build shopping list** button sits on the week plan and the list closes with a line
      pointing back at the plan that made it.

## Phase 5 — Jarvis and sharing polish

- [ ] Share the plan and the shopping list to a family chat in one tap.
- [ ] Jarvis answers from the set: "what can a 12-year-old make tonight?", "add a slow-cooker
      meal to Tuesday".

## Phase 6 — tests, docs, deploy

- [ ] Unit tests for filtering, staple ranking, scaling, plan parsing and list merging
      (backend pytest where pure; UI vitest for the plan/list generators).
- [ ] UI tests: share-to-chat on a recipe, plan editor writes the note, list keeps ticks.
- [ ] `npm run typecheck`, `npm run lint`, full test suites, `npm run build`.
- [ ] Update `docs/FAMILY_HUB.md` (Recipes section) and this doc; commit; deploy
      `execution gateway ui` as needed; verify live.

## Phase 7 — Skylight integration

The family's Skylight Calendar has its own meal planning (Meals tab + Recipe
Box + grocery list). Skylight has no official API, but its mobile app's API is
reverse-engineered and documented by the community (JSON:API on
`api.ourskylight.com`). This phase is optional and fail-safe: when no Skylight
account is connected, everything above still works.

- [ ] **Connect** — Settings card: Skylight email + password. Sign in with
      `POST /sessions` (`User-Agent: SkylightMobile (web)`) and keep the
      token with the user's credentials; list frames with `GET /frames` and
      let the family pick one (default: the only frame).
- [ ] **Connector** — small fail-safe module wrapping the endpoints: create a
      Recipe Box recipe (`POST /frames/{id}/meals/recipes` with
      `meal_category_id`, `summary`, `description` = `Ingredients:` block +
      `Instructions:` block); plan a meal (`POST /frames/{id}/meals/sittings`
      — when `meal_recipe_id` is set, leave `summary` blank or the API
      returns 422); read the week (`GET .../meals/sittings?date_min&date_max`);
      push a recipe's ingredients to the Skylight grocery list
      (`POST .../add_to_grocery_list`), or write items directly
      (`POST .../lists/{listId}/items`; delete only via
      `DELETE .../list_items/bulk_destroy`).
- [ ] **Recipes → Skylight** — "Send to Skylight" on a recipe card and in the
      editor pushes it to the Recipe Box under Dinner (or the picked meal
      category); sending again updates the same recipe instead of duplicating.
- [ ] **Meal plan → Skylight** — "Push week" writes each planned day as a
      dinner sitting (`add_to_grocery_list` off by default); shows what
      landed and surfaces failures as a toast, never a broken page.
- [ ] **Shopping list → Skylight** — optional "Send to Skylight list" mirrors
      the shopping list onto the frame's grocery list so it is tickable on the
      display too.
- [ ] **Fallback without credentials** — publish the week's dinners as
      calendar events into a calendar Skylight already syncs (Google/iCloud or
      a public ICS feed); the Skylight's unique email can also receive events.
- [ ] **Tests** — fake-HTTP tests for sessions, frames, recipes, sittings
      (blank-`summary` 422, array `data`, `instances` lagging) and grocery
      list bulk delete; UI tests for connect, send-to-Skylight and push-week.
- [ ] **Note** — the API is unofficial and may change with app updates: keep
      the connector optional, conservative on rate, and never block the local
      flows on it.

## Phase 8 — search and import

- [ ] Search sources: the seeded CC0 set itself (a SQLite FTS index over title,
      ingredients and tags, so search stays offline and instant), TheMealDB's free API for
      a quick lookup with photos, and "paste a link" import that reads a page's schema.org
      `Recipe` JSON-LD.
- [ ] Backend: a small search service or execution handler that builds the index from the
      imported dataset, with `search`, `fetch` and `url-import` endpoints behind
      `/api/recipes/*`; conservative rate limits; never block on a dead source.
- [ ] UI: a search box on Recipes with results (photo, title, time, servings, source), a
      preview sheet (ingredients + steps), and **Save to Recipes** writing the note with the
      standard header, guessed labels (`#level-…` from time and ingredient count,
      `#slow-cooker`/`#instant-pot` from the title, `#30-minutes`, `#5-ingredients`) and
      the source URL + licence line.
- [ ] Duplicate guard: skip a result whose source URL is already imported.
- [ ] Tests: index search and JSON-LD parsing (pytest, fixture page), search → preview →
      save (vitest).

## Phase 9 — photo and handwriting import

- [ ] Capture: `<input type="file" accept="image/*" capture="environment">` for now (no
      Capacitor camera plugin is installed; it can come later for a nicer native flow).
      Keep the photo by uploading it to Nextcloud with the existing `provider.upload_file`
      and link it from the note, so the original page stays with the recipe.
- [ ] Vision: the family's **Alpaca** server (`/Users/jeremiahsummers/Code/alpaca`, the
      Ollama-compatible `llama.cpp` proxy behind `llm_local_url`) already has vision. Use
      its document-AI OCR endpoint — `POST /api/vision/ocr` on the Alpaca web backend
      (port 5000, Qwen2.5-VL with automatic VL fallback) — which is built for exactly this
      (photos of pages, headlines/structure, handwriting). The Ollama-compatible
      `/api/chat` also passes a message `images` array through to `llama.cpp` as
      `image_data` when a VL model with an `mmproj` projector is loaded (those models
      report `capabilities: ['vision']`; `alpaca-puller` wires `*mmproj*.gguf` companions
      automatically, and a Qwen3VL-8B mmproj companion is already present). A
      `vision_model` setting (empty = feature off) can name the VL model when going
      through `llm_local_url` — that path needs the gateway `OllamaProvider` extended to
      send images. No cloud key is required; an OpenRouter key remains an optional cloud
      alternative, with a clear note that photos would leave the house.
- [ ] Pipeline: photo → prompt for strict JSON (title, servings, prep/cook/total time,
      ingredients with quantities, steps, notes, uncertain words marked) → **editable
      draft** beside the raw transcription → Save to Recipes (`#photo-import` label).
- [ ] Handwriting: ask the model to flag low-confidence words, keep the raw text for
      comparison, and never save without a review.
- [ ] Errors: no vision model configured → one-line explanation and the typing path stays.
- [ ] Tests: stub the vision provider with a fixed sample page (and a handwriting-style
      response); UI draft edit + save test.

## Phase 10 — Health tab connection

- [ ] Carry per-serving nutrition from the dataset into each imported recipe note
      (calories, protein, carbs, fat, fiber, sugar, sodium — rescaling the corpus
      panel to the note's 7 servings) and show it on the recipe preview/editor.
      Edited or computed recipes fall back to the USDA-seed pipeline.
- [ ] Health tab: a **Meals** card showing today's planned meals from
      `Recipes/Meal plan.md` with an estimated total, scaled to the household servings.
- [ ] Ticking a meal cooked logs it for the day, so Health shows planned vs. eaten.
- [ ] Optional (decide with the family): daily calorie/protein targets, weekly averages,
      and per-person portions.
- [ ] Stay out of the way when there is no plan or nutrition data: hide the card.
- [ ] Tests: nutrition parsing and scaling (pure), plan → health day mapping, the card
      and mark-cooked UI.
- [ ] Docs and deploy.

## Phase 11 — calorie budget and logging (Noom-style)

- [ ] Per-person nutrition profile in the Health tab: age, height, weight, sex and
      activity level. Adults get a daily calorie budget from Mifflin-St Jeor BMR ×
      activity (minus a gentle deficit only when they ask); kids and teens get
      age-appropriate guidance ranges, never weight-loss deficits.
- [ ] Log meals: ticking a planned meal cooked adds its per-serving nutrition to the
      day (portion picker ½, 1, 1½ …); quick-add other foods from the imported set
      or by hand.
- [ ] Day view: calories eaten vs budget, protein/carbs/fat totals, and a simple
      density colour (green / yellow / orange) like Noom's food colours.
- [ ] Optional: weight trend for adults, weekly averages, per-person portions.
- [ ] Keep it local: no coaching, no cloud, no streaks.
- [ ] Tests: BMR/budget maths (pure), portion scaling, day totals, the day view UI.
- [ ] Docs and deploy.

## Acceptance

- [ ] 100+ imported recipes on the Recipes page, filterable by level (and by appliance
      label when present).
- [ ] Family tab reaches Recipes; a recipe shares to a chat in one tap.
- [ ] A week's plan produces a tappable shopping list that survives regeneration.
- [ ] Jarvis answers dinner questions from the imported set in chat.
