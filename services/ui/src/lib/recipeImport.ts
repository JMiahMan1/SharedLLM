// Reads a pasted recipe link into a note. The server fetches the page and
// returns its schema.org recipe; this module guesses the shelf labels and
// renders the note body (the notes service adds the title/category header).

/** Per-serving nutrition as the source stated it; a missing name was not stated. */
export interface StatedNutrition {
  calories?: number;
  fat_g?: number;
  sat_fat_g?: number;
  cholesterol_mg?: number;
  sodium_mg?: number;
  carbs_g?: number;
  fiber_g?: number;
  sugar_g?: number;
  protein_g?: number;
}

export interface ImportedRecipe {
  title: string;
  description: string;
  ingredients: string[];
  instructions: string[];
  prep_minutes: number | null;
  cook_minutes: number | null;
  total_minutes: number | null;
  yield: string;
  image: string | null;
  source_url: string;
  site: string;
  /** Free notes the model read off a photo (unused for link/search imports). */
  notes?: string;
  /** Where the draft came from; a photo import adds its own label. */
  source_kind?: 'link' | 'search' | 'photo';
  /** Per serving: the Food.com submitter's figures, or a page's published ones. */
  nutrition?: StatedNutrition | null;
}

/** One card from recipe search: the offline index or TheMealDB. */
export interface RecipeSearchItem {
  id?: string | number | null;
  source?: 'foodcom' | 'themealdb';
  title: string;
  description?: string;
  image?: string | null;
  total_minutes?: number | null;
  servings?: number | null;
  yield?: string;
  site?: string;
  ingredients?: string[];
  instructions?: string[];
  prep_minutes?: number | null;
  cook_minutes?: number | null;
  source_url: string;
  /** Per serving, as stated (offline index only). */
  calories?: number | null;
  protein_g?: number | null;
}

/** Shelf labels for an imported recipe: level first, then the quick ones. */
export function guessLabels(recipe: ImportedRecipe): string[] {
  const labels: string[] = [];
  const title = recipe.title.toLowerCase();
  const total =
    recipe.total_minutes ??
    ((recipe.prep_minutes ?? 0) + (recipe.cook_minutes ?? 0) || null);
  const ingredientCount = recipe.ingredients.length;
  const stepCount = recipe.instructions.length;

  if (
    ingredientCount > 0 &&
    ingredientCount <= 8 &&
    stepCount > 0 &&
    stepCount <= 8 &&
    total !== null &&
    total <= 30
  ) {
    labels.push('#level-1');
  } else if (ingredientCount <= 12 && (total === null || total <= 60)) {
    labels.push('#level-2');
  } else {
    labels.push('#level-3');
  }

  if (/slow[ -]?cooker|crock[ -]?pot/.test(title)) labels.push('#slow-cooker');
  if (/instant[ -]?pot|pressure[ -]?cook/.test(title)) labels.push('#instant-pot');
  if (total !== null && total <= 35) labels.push('#30-minutes');
  if (ingredientCount > 0 && ingredientCount <= 5) labels.push('#5-ingredients');
  return labels;
}

/** The label a photo or handwriting import carries. */
export const PHOTO_LABEL = '#photo-import';

/** Written in this order, the way scripts/import_recipes.py writes them. */
const NUTRITION_PARTS: [keyof StatedNutrition, string][] = [
  ['calories', 'kcal'],
  ['fat_g', 'g fat'],
  ['sat_fat_g', 'g sat fat'],
  ['cholesterol_mg', 'mg cholesterol'],
  ['sodium_mg', 'mg sodium'],
  ['carbs_g', 'g carbs'],
  ['fiber_g', 'g fiber'],
  ['sugar_g', 'g sugar'],
  ['protein_g', 'g protein'],
];

const round1 = (value: number) => Math.round(value * 10) / 10;

/**
 * The `## Nutrition` line for a recipe, in the format `parseRecipeNutrition`
 * reads (so it can be logged to Health), or null when nothing was stated.
 */
export function nutritionLine(recipe: Pick<ImportedRecipe, 'nutrition' | 'site'>): string | null {
  const stated = recipe.nutrition ?? {};
  const parts = NUTRITION_PARTS.filter(([key]) => typeof stated[key] === 'number').map(
    ([key, label]) => `${round1(stated[key] as number)} ${label}`,
  );
  if (parts.length === 0) return null;
  const who = recipe.site === 'food.com' ? 'the submitter - foodcom' : 'the recipe page - link';
  return `*Per serving, as stated by ${who}:* ${parts.join(' · ')}`;
}

/** The note body for an imported recipe (title/category come from the save). */
export function buildRecipeNote(recipe: ImportedRecipe, extraLabels: string[] = []): string {
  const yieldText = recipe.yield
    ? /serv/i.test(recipe.yield)
      ? recipe.yield
      : `servings ${recipe.yield}`
    : '';
  const times = [
    recipe.prep_minutes ? `prep ${recipe.prep_minutes} min` : '',
    recipe.cook_minutes ? `cook ${recipe.cook_minutes} min` : '',
    recipe.total_minutes ? `total ${recipe.total_minutes} min` : '',
    yieldText,
  ]
    .filter(Boolean)
    .join(' · ');

  const labels = [...guessLabels(recipe), ...extraLabels];
  const source = recipe.source_url
    ? `Source: ${recipe.source_url} - imported from ${recipe.site}`
    : `Source: ${recipe.site}`;

  const lines = [`Tags: ${labels.join(' ')}`];
  if (times) lines.push(`Times: ${times}`);
  lines.push(
    source,
    '',
    '## Ingredients',
    ...recipe.ingredients.map((item) => `- [ ] ${item}`),
    '',
    '## Steps',
    ...recipe.instructions.map((step, index) => `${index + 1}. ${step}`),
  );
  const nutrition = nutritionLine(recipe);
  if (nutrition) lines.push('', '## Nutrition', nutrition);
  if (recipe.notes) lines.push('', '## Notes', recipe.notes);
  return lines.join('\n');
}
