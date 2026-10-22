# Recipes and meal planning

Status: **planned — nothing built yet.** Last live app commit `152451cf` (v1.5.1).

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

---

## Phase 0 — decisions

- [ ] Importer home: one-off `tools/import_recipes.py` run from a workstation (proposed),
      or an Admin › Recipes button.
- [ ] Dataset access: Hugging Face mirror or Kaggle CLI token for
      `irkaal/foodcom-recipes-and-reviews` (522k recipes, **CC0**); fallback
      `shuyangli94/food-com-recipes-with-search-terms-and-tags` for tags/search terms.
- [ ] Confirm the level rules, staple list and household serving count (7).
- [ ] Confirm the meal-plan note format below.

## Phase 1 — recipe importer

- [ ] Download the dataset (CSV/parquet, ~1.5 GB) once; keep it out of the repo.
- [ ] Filter candidates: tag matches for each level, `crock-pot-slow-cooker`,
      pressure/Instant Pot titles, `30-minutes-or-less`, `5-ingredients-or-less`,
      `inexpensive`. Drop dessert/drink noise; keep mains, breakfasts and lunches.
- [ ] Rank by staple overlap + few ingredients + short total time; pick a balanced set per
      level and appliance (~40 level-1, ~40 level-2, ~40 level-3).
- [ ] Render markdown: header, labels, scaled ingredient list (`- [ ] 2 lb chicken`),
      numbered steps, source/licence line.
- [ ] Guarantee unique note titles (`Title.md`; suffix ` (2)` on collision).
- [ ] Wikibooks Cookbook: pull `Category:Slow cooker recipes` (~26 pages) through the
      MediaWiki API, convert to markdown, keep the CC BY-SA credit line.
- [ ] Dry-run report first: counts per level/appliance and a sample note for review,
      before anything is written to Nextcloud.

## Phase 2 — Recipes page touchpoints

- [ ] **Share to chat** on recipe cards and in the editor — reuse the existing
      `ShareToChatSheet` the Bible reader uses (reference + text into a family chat).
- [ ] Family tab: link/section to Recipes if it is not reachable from there today.
- [ ] "Teach Jarvis" RAG sync over `Recipes/` if it is missing (and run it after import).

## Phase 3 — weekly meal plan

- [ ] Format: `Recipes/Meal plan.md` — header + `#meal-plan` + one checklist line per day,
      e.g. `- [ ] Mon · Chicken Chili`. Ticking a day marks it cooked.
- [ ] Plan view (Recipes tab or Family card): 7 slots, pick a recipe per day from the
      shelf; writes the note like any other edit.
- [ ] Shows each pick's level and appliance; quick "surprise me" and repeat warnings.

## Phase 4 — shopping list

- [ ] Generate from the plan's recipes: aggregate ingredients, merge duplicates, scale to 7,
      optionally group by aisle.
- [ ] Write `Recipes/Shopping list.md` as a `- [ ]` checklist note (tappable on cards).
- [ ] Regenerate when the plan changes and **keep ticked items** (merge by item text).
- [ ] Button on the meal plan; the list links back to the plan that made it.

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
- [ ] Vision: a `vision_model` setting (empty = feature off) plus an Ollama multimodal call
      (a message `images` array with base64 bytes). The house's model today
      (`qwen3-6-35b-a3b`) is text-only, so the admin pulls a multimodal model
      (qwen2.5-vl, llama3.2-vision or llava) on the desktop; an OpenRouter key can stand in
      for cloud vision, with a clear note that photos would leave the house.
- [ ] Pipeline: photo → prompt for strict JSON (title, servings, prep/cook/total time,
      ingredients with quantities, steps, notes, uncertain words marked) → **editable
      draft** beside the raw transcription → Save to Recipes (`#photo-import` label).
- [ ] Handwriting: ask the model to flag low-confidence words, keep the raw text for
      comparison, and never save without a review.
- [ ] Errors: no vision model configured → one-line explanation and the typing path stays.
- [ ] Tests: stub the vision provider with a fixed sample page (and a handwriting-style
      response); UI draft edit + save test.

## Acceptance

- [ ] 100+ imported recipes on the Recipes page, filterable by level and appliance.
- [ ] Family tab reaches Recipes; a recipe shares to a chat in one tap.
- [ ] A week's plan produces a tappable shopping list that survives regeneration.
- [ ] Jarvis answers dinner questions from the imported set in chat.
