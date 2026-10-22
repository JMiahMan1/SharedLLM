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

## Acceptance

- [ ] 100+ imported recipes on the Recipes page, filterable by level and appliance.
- [ ] Family tab reaches Recipes; a recipe shares to a chat in one tap.
- [ ] A week's plan produces a tappable shopping list that survives regeneration.
- [ ] Jarvis answers dinner questions from the imported set in chat.
