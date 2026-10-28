# Recipes and meal planning

Status: **Phases 0-5 and 8-10 built** - the importer (`scripts/import_recipes.py`) renders the
first level-balanced note set; the Recipes page (plan, shopping list, search, link and
photo import, the duplicate-import guard) and the Health connection (recipe logging, the
Dinner tonight card with a planned-vs-eaten comparison and a USDA-seed estimate, and the
editor's nutrition summary) are live. Phase 7 (Skylight), the assistant's write action in
Phase 5, staple-overlap ranking and Phase 11's per-person budget are still open. The
meal-planning half of Phase 6 - plan parsing and grocery-list merging - is built and tested:
the plan editor and the shopping list are plain markdown under `Recipes/`, and
`services/ui/src/lib/mealPlan.ts` / `shoppingList.ts` parse and rebuild them. Staple-overlap
ranking is the one part not built, because the shelf lists titles and the list is assembled by
fetching each uncooked recipe's body - a ranking needs an ingredient index first. Live: version
1.6.0 (the deployed commit is in `HANDOVER.md` §0, which is the file that tracks it).

Goal: fill the family's Recipes shelf with kid-tiered, fast and appliance-friendly meals,
then turn them into a weekly plan and a shared shopping list. Every artefact stays a plain
markdown note under `Recipes/` in Nextcloud, so the Recipes page, Notes search, chat sharing
and Jarvis memory all work with no new format.

## Ground rules

- Notes keep the standard header the app writes - `# Title`, `Category: Recipes`, blank
  line - with labels as inline `#tags`. Nextcloud Notes compatibility stays intact.
- Imported recipes carry: `#level-1`, `#level-2`, `#level-3`, `#slow-cooker`,
  `#instant-pot`, `#30-minutes`, `#5-ingredients`.
- All quantities are scaled to **7 servings** (family of 7; tweak per recipe in the note).
- Every imported recipe ends with a one-line source and licence note
  (Food.com - CC0, or Wikibooks Cookbook - CC BY-SA).

## Level rules (proposed)

| Level | Ages | Shape of the recipe | Source tags used |
| --- | --- | --- | --- |
| `#level-1` | 7-10 | No-cook / assembly / microwave, no sharp knives or heat | `no-cook`, `5-ingredients-or-less`, `easy` + short steps |
| `#level-2` | 11-14 | Stovetop and oven basics, simple knife work, ≤ 30 min | `easy`, `beginner-cook`, `30-minutes-or-less` |
| `#level-3` | 15-18 | Full meals: oven roasts, pressure cooker, multi-step | remainder, `crock-pot-slow-cooker`, pressure cooker titles |

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
  note, tagged `foodcom` - "as stated by the submitter", not lab ground truth.
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

## Phase 0 - decisions

- [x] Importer home: `scripts/import_recipes.py`, a one-off workstation script
      (repo convention - `tools/` is reserved for execution-image subprocess tools).
- [x] Dataset access: `kagglehub`'s anonymous download works - no Kaggle account or
      token needed; `kagglehub.dataset_download("irkaal/foodcom-recipes-and-reviews")`
      (~723 MB) caches under `~/.cache/kagglehub/`, kept out of the repo. Nutrition
      columns are **per serving**: recipe #38's 170.9 kcal matches the live food.com
      JSON-LD byte for byte. The shuyangli94 fallback was not needed.
- [ ] Confirm the level rules, staple list and household serving count (7).
- [ ] Confirm the meal-plan note format below.

## Phase 1 - recipe importer

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
- [x] Guarantee unique note titles (`Title.md`; suffix `(2)` on collision).
- [ ] Wikibooks Cookbook (bonus): the `Category:Slow cooker recipes` set (~26 pages) through
      the MediaWiki API, converted to markdown with the CC BY-SA credit line.
- [x] Dry-run report first: funnel + sample notes printed before anything is
      written (522,517 rows → 339,044 with servings → 246,772 non-noise → 213,716
      ingredient/step bounds → 188,908 no alcohol; pools L1 2,253 / L2 56,171 /
      L3 88,426).

## Phase 2 - Recipes page touchpoints

- [x] **Share to chat** on recipe cards and in the editor - reuses the Bible
      reader's `ShareToChatSheet`, generalized with an optional title, icon and
      message builder. The chat message is a short `🍽 title: excerpt` with an
      optional intro note, never the whole file.
- [x] Family tab: Recipes is reachable today (Family → Create renders the
      page); a dedicated link can come with the meal-plan view.
- [x] "Teach Jarvis" RAG sync over `Recipes/` - a header button on the Recipes
      page calls the notes `sync_rag` route for the recipes folder.

## Phase 3 - weekly meal plan (shipped)

- [x] Format: `Recipes/Meal plan.md` - header + `#meal-plan` + one checklist line per day,
      e.g. `- [ ] Mon · Chicken Chili`. Ticking a day marks it cooked.
- [x] Plan view on the Recipes page (`Week plan`): 7 slots, pick a recipe per day from the
      shelf; **Save plan** writes the note like any other edit.
- [x] Shows each pick's level (from the note's `#level-N` tag); a quick "surprise me" fill
      and a warning when a recipe repeats in the week. Appliance chips
      (`#slow-cooker`/`#instant-pot`) still open.

## Phase 4 - shopping list (shipped)

- [x] Generated from the plan's recipes: the ingredients of each planned dinner are
      aggregated and duplicates are merged. Imported notes are already scaled to 7 servings.
- [x] Written to `Recipes/Shopping list.md` as a `- [ ]` checklist note, tappable like any note.
- [x] Regenerating keeps every ticked item (matched by item text); items ticked earlier that
      are no longer planned stay ticked at the bottom.
- [x] A **Build shopping list** button sits on the week plan and the list closes with a line
      pointing back at the plan that made it.

## Phase 5 - Jarvis and sharing polish

- [x] Share the plan and the shopping list to a family chat in one tap - Share plan and
      Share list buttons on the week plan open the same chat sheet recipes use; the message
      is a short summary (`🍽 This week's dinners:` / `🛒 Shopping list:`, capped at 25 items).
- [ ] Jarvis answers from the set: "what can a 12-year-old make tonight?", "add a slow-cooker
      meal to Tuesday". Reading works through Teach Jarvis (the notes RAG sync over
      `Recipes/`); the write action needs assistant tooling and is still open.

## Phase 6 - tests, docs, deploy

Shipped since this list was written: `services/ui/src/test/recipeImport.test.ts`,
`recipeNutrition.test.ts`, `recipeEaten.test.ts` and `recipeShare.test.ts` cover the importer,
the nutrition estimate, the Dinner-tonight comparison and share-to-chat, and
`services/ui/src/pages/Recipes.test.tsx` covers the page (including the photo import).
`services/ui/src/lib/mealPlan.ts` and `shoppingList.ts`, with `test/mealPlan.test.ts` and
`test/shoppingList.test.ts`, cover plan parsing, surprise fill and list merging;
`pages/RecipesPlan.test.tsx` covers the day pick, cooked tick, save, build list, share and dinner
log. The typecheck / lint / full-suite / build gates run on every change, and the browser suite
runs nightly in CI.

- [x] Unit tests for filtering, scaling, plan parsing and list merging (backend pytest where pure;
      UI vitest for the plan/list generators).
- [x] UI tests: share-to-chat on a recipe, plan editor writes the note, list keeps ticks.
- [x] `npm run typecheck`, `npm run lint`, full test suites, `npm run build`.
- [x] Update `docs/FAMILY_HUB.md` (Recipes section) and this doc.
- [ ] Staple-overlap ranking: not built. The shelf API lists titles, and the shopping list fetches
      each uncooked recipe's body, so ranking needs an ingredient index (or a server-side ingredient
      field) and a place to show it. Left out rather than guessed at.
- [ ] Deploy and verify live after the next Recipes change (a doc-only pass needs no deploy).

## Phase 7 - Skylight integration

The family's Skylight Calendar has its own meal planning (Meals tab + Recipe
Box + grocery list). Skylight has no official API, but its mobile app's API is
reverse-engineered and documented by the community (JSON:API on
`api.ourskylight.com`). This phase is optional and fail-safe: when no Skylight
account is connected, everything above still works.

- [ ] **Connect** - Settings card: Skylight email + password. Sign in with
      `POST /sessions` (`User-Agent: SkylightMobile (web)`) and keep the
      token with the user's credentials; list frames with `GET /frames` and
      let the family pick one (default: the only frame).
- [ ] **Connector** - small fail-safe module wrapping the endpoints: create a
      Recipe Box recipe (`POST /frames/{id}/meals/recipes` with
      `meal_category_id`, `summary`, `description` = `Ingredients:` block +
      `Instructions:` block); plan a meal (`POST /frames/{id}/meals/sittings`
      - when `meal_recipe_id` is set, leave `summary` blank or the API
      returns 422); read the week (`GET .../meals/sittings?date_min&date_max`);
      push a recipe's ingredients to the Skylight grocery list
      (`POST .../add_to_grocery_list`), or write items directly
      (`POST .../lists/{listId}/items`; delete only via
      `DELETE .../list_items/bulk_destroy`).
- [ ] **Recipes → Skylight** - "Send to Skylight" on a recipe card and in the
      editor pushes it to the Recipe Box under Dinner (or the picked meal
      category); sending again updates the same recipe instead of duplicating.
- [ ] **Meal plan → Skylight** - "Push week" writes each planned day as a
      dinner sitting (`add_to_grocery_list` off by default); shows what
      landed and surfaces failures as a toast, never a broken page.
- [ ] **Shopping list → Skylight** - optional "Send to Skylight list" mirrors
      the shopping list onto the frame's grocery list so it is tickable on the
      display too.
- [ ] **Fallback without credentials** - publish the week's dinners as
      calendar events into a calendar Skylight already syncs (Google/iCloud or
      a public ICS feed); the Skylight's unique email can also receive events.
- [ ] **Tests** - fake-HTTP tests for sessions, frames, recipes, sittings
      (blank-`summary` 422, array `data`, `instances` lagging) and grocery
      list bulk delete; UI tests for connect, send-to-Skylight and push-week.
- [ ] **Note** - the API is unofficial and may change with app updates: keep
      the connector optional, conservative on rate, and never block the local
      flows on it.

## Phase 8 - search and import (paste-a-link + TheMealDB shipped)

- [x] Search sources: the offline index over the CC0 Food.com corpus first (SQLite FTS
      over name, keywords and ingredients - instant, no network; built by
      `scripts/build_recipe_seed.py` into `data/recipes/recipe-seed.db`), TheMealDB's free
      API for looks with photos, and "paste a link" import that reads a page's schema.org
      `Recipe` JSON-LD. All three are built (`/execute/recipes` behind
      `/api/recipes/import`, `/api/recipes/search` and `/api/recipes/fetch`); the Recipes
      page filters the shelf by name as you type.
- [x] Backend: `url_import` lives in `services/execution/handlers/recipe_import.py` - it
      fetches the page (http/https only, 25 s timeout, 1.5 MB cap) and reads the first
      `Recipe` node (arrays and `@graph` included), returning title, ingredients, ordered
      steps (sections flattened), prep/cook/total minutes, yield, image and the source URL;
      pages without recipe data get a plain message instead of a guess. The free TheMealDB
      API (`search.php`) is read inside `recipe_search` (`_themealdb_hits`), which maps each
      meal into the same shape - measured ingredient pairs, steps split on line breaks,
      source URL `/meal/{id}`. `recipe_search` answers from the offline index first
      (TheMealDB only fills what is left, and either can fail without taking the page down)
      and `shelf_fetch` reads one full recipe from the index by id. The standalone
      `themealdb_search` action was reachable from no route and has been removed.
- [x] UI: **Import a link** on the Recipes page - paste, read, preview (title, site, time,
      yield, counts) and **Save to Recipes** writes the note with the standard header, guessed
      labels (`#level-…` from time and ingredient count, `#slow-cooker`/`#instant-pot` from
      the title, `#30-minutes`, `#5-ingredients`) and the source URL line. The same panel
      searches recipes - Food.com hits read their full ingredients and steps from the
      offline index, TheMealDB hits show a photo; both pick into the same preview and save
      (source lines point back at food.com or themealdb.com). A name filter above the shelf
      narrows the list as you type.
- [x] Duplicate guard: a search result whose source URL is already imported is marked
      **Already in Recipes** and cannot be picked again. The guard is exact, not a
      preview heuristic: `recipe_sources` (execution `handlers/recipe_import.py`) lists
      `Recipes/`, reads every note's `Source:` line (cached by the note's version, so a
      repeat search only re-reads what changed) and returns the URLs; the gateway route
      is `POST /api/recipes/sources`. A pasted link whose URL is already on the shelf is
      refused on save too. Tests: `services/execution/tests/test_recipe_import.py`,
      `services/gateway/tests/test_recipe_gateway.py`, `src/pages/Recipes.test.tsx`.
- [x] Tests: JSON-LD parsing and the handler (pytest, fixture page -
      `services/execution/tests/test_recipe_import.py`), label guessing and note rendering
      (`src/test/recipeImport.test.ts`) and paste → read → preview → save
      (`src/pages/Recipes.test.tsx`). Index search tests come with the index.

## Phase 9 - photo and handwriting import

- [x] Capture: `<input type="file" accept="image/*" capture="environment">` (no
      Capacitor camera plugin is installed; it can come later for a nicer native flow).
      The photo is kept by uploading it to Nextcloud with the existing
      `noteAttachment` upload and linking it from the note, so the original page
      stays with the recipe (best effort - the recipe saves first).
- [x] Vision: the family's **Alpaca** server - the same local vision OCR the
      workspaces, Raven and the Health food label already use (`llm_local_url`
      plus the `vision_ocr_model` setting; no cloud key, nothing leaves the
      house). The recipe photo runs execution's `/execute/ocr` with
      `task="recipe"`; a new prompt in
      `services/execution/handlers/vision_ocr.py` asks for strict JSON and the
      uncertain words. No vision model configured fails with a one-line
      explanation and the typing path stays.
- [x] Pipeline: photo → `services/common/recipe_ocr.py` normalizes the model's
      reading (title, servings, prep/cook/total time, ingredients, steps, notes,
      uncertain words) → an **editable draft** (a textarea) beside the raw
      transcription → Save to Recipes (`#photo-import` label), gateway
      `POST /api/recipes/photo`.
- [x] Handwriting: the model flags low-confidence words, the raw text stays for
      comparison, and nothing saves without the person reviewing the draft.
- [x] Errors: no vision model configured → one-line explanation and the typing
      path stays; a photo with nothing readable names the parts that are missing
      rather than inventing them.
- [x] Tests: the parser (`services/common/tests/test_recipe_ocr.py`), the
      gateway (stubbed OCR - `services/gateway/tests/test_recipe_gateway.py`)
      and the UI draft edit + save + photo-keep
      (`src/pages/Recipes.test.tsx`).

## Phase 10 - Health tab connection

- [x] Per-serving nutrition in each imported recipe note: `scripts/import_recipes.py`
      writes the corpus panel (calories, protein, carbs, fat, fiber, sugar, sodium,
      rescaled to the note's 7 servings) as a `foodcom`-tagged line. The Recipes
      editor now shows that line back, per serving and as a whole-recipe estimate
      scaled to the servings the note states (`parseRecipeServings`), so the numbers
      are visible without opening the log sheet.
- [x] Edited or computed recipes fall back to the USDA-seed pipeline: when a note states
      no nutrition, the Recipes editor offers **Estimate from ingredients (USDA)**.
      `services/health/recipe_estimate.py` splits each ingredient line into a quantity, a
      unit and a name, matches the name in the USDA seed (`services/health/food_seed.py`,
      `data/health/fdc-seed.db`) and weighs it with an FNDDS household portion
      (N = V × W / 100); mass units convert directly. It is labelled an estimate and
      never the recipe's own numbers, and every ingredient the seed cannot match (or
      weigh) is shown as **unknown** with the reason, never a guessed number; a total
      built from only some lines says so. The offer is hidden in kid mode. Tests:
      `services/health/tests/test_recipe_estimate.py`,
      `services/gateway/tests/test_health_hub_gateway.py`, `src/pages/Recipes.test.tsx`.
- [x] Health tab: a **Dinner tonight** card (`MealsCard`, Health → Today) shows
      today's planned dinner from `Recipes/Meal plan.md` with its stated per-serving
      nutrition and a whole-recipe estimate scaled to the note's own servings, and a
      **Log tonight's dinner** button. It hides when nothing is planned or the
      nutrition card is switched off, and its numbers hide in kid mode. The estimate
      uses the note's stated servings rather than a hardcoded household count (the
      household serving count is still an unconfirmed Phase 0 decision).
- [x] Ticking a meal cooked logs it for the day, so Health shows planned vs. eaten.
      Logging is built, as a button rather than the tick: **Log tonight's dinner** on
      today's row of the week plan (and the Dinner tonight card), and **Log to Health**
      on an open recipe, post the note's per-serving nutrition with a portion picker
      (see HEALTH_HUB.md Phase 4). Logging tonight's dinner from the plan now also ticks
      that day cooked in the plan (an option in the log sheet, on by default), and the
      shopping list leaves cooked dinners out (HEALTH_HUB.md Phase 8). The **Dinner
      tonight** card now shows what was actually logged for dinner today beside the plan
      - tonight's recipe logged as itself, something else logged instead, or nothing yet
      - via `src/lib/recipeEaten.ts`; its numbers hide in kid mode. Tests:
      `src/test/recipeEaten.test.ts`, `src/components/health/MealsCard.test.tsx`.
- [ ] Optional (decide with the family): daily calorie/protein targets, weekly averages,
      and per-person portions.
- [x] Stay out of the way when there is no plan or nutrition data: the Dinner tonight
      card returns nothing when no dinner is planned.
- [x] Tests: nutrition parsing, scaling and the note's stated servings (pure -
      `src/test/recipeNutrition.test.ts`), the plan's today mapping and the log sheet
      (`LogRecipeSheet.test.tsx`, `RecipesPlan.test.tsx`), the Dinner tonight card and
      its planned-vs-eaten comparison (`MealsCard.test.tsx`, `src/test/recipeEaten.test.ts`)
      and the USDA estimate (`services/health/tests/test_recipe_estimate.py`,
      `src/pages/Recipes.test.tsx`).
- [x] Docs and deploy (live in 1.6.0, 2026-10-27).

## Phase 11 - calorie budget and logging (Noom-style)

- [x] Per-person nutrition profile in the Health tab: age, height, weight, sex and
      activity level (`HealthProfilePanel.tsx`, `services/health/scoring.py`). Adults get a
      daily calorie budget from Mifflin-St Jeor BMR × activity (minus a gentle deficit only
      when they ask); kids and teens get age-appropriate guidance ranges, never weight-loss
      deficits (`calorie_target`, `macro_targets`).
- [x] Log meals: a planned meal adds its per-serving nutrition to the day (portion
      picker ½, 1, 1½, 2) from the week plan's **Log tonight's dinner** or a recipe's
      **Log to Health**; a recipe without stated nutrition is entered by hand. Other
      foods are quick-added from Health → Nutrition.
- [x] Day view: calories eaten vs budget, protein/carbs/fat totals, and a simple
      density colour (green / yellow / orange) like Noom's food colours (`food_density` in
      `services/health/scoring.py`, rendered in `HealthNutrition.tsx`).
- [x] Optional: weight trend for adults, weekly averages (`services/health/weekly.py`),
      per-person portions.
- [x] Keep it local: no coaching, no cloud, no streaks.
- [x] Tests: BMR/budget maths (`services/health/tests/test_scoring.py`), portion scaling,
      day totals, the day view UI (`src/components/health/HealthNutrition.test.tsx`).
- [x] Docs and deploy.

## Acceptance

- [x] 100+ imported recipes on the Recipes page, filterable by level (and by appliance
      label when present).
- [x] Family tab reaches Recipes; a recipe shares to a chat in one tap.
- [x] A week's plan produces a tappable shopping list that survives regeneration.
- [x] Jarvis answers dinner questions from the imported set in chat.
