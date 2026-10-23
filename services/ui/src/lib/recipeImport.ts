// Reads a pasted recipe link into a note. The server fetches the page and
// returns its schema.org recipe; this module guesses the shelf labels and
// renders the note body (the notes service adds the title/category header).

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

/** The note body for an imported recipe (title/category come from the save). */
export function buildRecipeNote(recipe: ImportedRecipe): string {
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

  const lines = [`Tags: ${guessLabels(recipe).join(' ')}`];
  if (times) lines.push(`Times: ${times}`);
  lines.push(
    `Source: ${recipe.source_url} — imported from ${recipe.site}`,
    '',
    '## Ingredients',
    ...recipe.ingredients.map((item) => `- [ ] ${item}`),
    '',
    '## Steps',
    ...recipe.instructions.map((step, index) => `${index + 1}. ${step}`),
  );
  return lines.join('\n');
}
