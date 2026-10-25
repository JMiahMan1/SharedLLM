import { describe, it, expect } from 'vitest';
import { PHOTO_LABEL, buildRecipeNote, guessLabels, type ImportedRecipe } from '../lib/recipeImport';

const RECIPE: ImportedRecipe = {
  title: 'Best Lemonade',
  description: '',
  ingredients: ['4 cups water', '1 cup lemon juice', '1/2 cup sugar'],
  instructions: ['Juice the lemons.', 'Stir and chill.'],
  prep_minutes: 15,
  cook_minutes: null,
  total_minutes: 25,
  yield: '6 servings',
  image: null,
  source_url: 'https://example.com/recipes/lemonade',
  site: 'example.com',
};

describe('recipeImport', () => {
  it('guesses the easy labels for a short recipe', () => {
    expect(guessLabels(RECIPE)).toEqual(['#level-1', '#30-minutes', '#5-ingredients']);
  });

  it('spots a slow cooker title and a middling recipe', () => {
    const chili: ImportedRecipe = {
      ...RECIPE,
      title: 'Slow Cooker Chili',
      ingredients: Array.from({ length: 9 }, (_, i) => `ingredient ${i + 1}`),
      total_minutes: 45,
    };
    expect(guessLabels(chili)).toEqual(['#level-2', '#slow-cooker']);
  });

  it('spots an instant pot title', () => {
    const rice: ImportedRecipe = {
      ...RECIPE,
      title: 'Instant Pot Rice',
      ingredients: Array.from({ length: 6 }, (_, i) => `ingredient ${i + 1}`),
      total_minutes: 20,
    };
    expect(guessLabels(rice)).toEqual(['#level-1', '#instant-pot', '#30-minutes']);
  });

  it('calls a big recipe level three', () => {
    const feast: ImportedRecipe = {
      ...RECIPE,
      ingredients: Array.from({ length: 15 }, (_, i) => `ingredient ${i + 1}`),
      instructions: Array.from({ length: 10 }, (_, i) => `step ${i + 1}`),
      total_minutes: 90,
    };
    expect(guessLabels(feast)).toEqual(['#level-3']);
  });

  it('writes the note body with tags, times, source, and checklists', () => {
    const note = buildRecipeNote(RECIPE);
    expect(note).toContain('Tags: #level-1 #30-minutes #5-ingredients');
    expect(note).toContain('Times: prep 15 min · total 25 min · 6 servings');
    expect(note).toContain('Source: https://example.com/recipes/lemonade - imported from example.com');
    expect(note).toContain('## Ingredients');
    expect(note).toContain('- [ ] 4 cups water');
    expect(note).toContain('1. Juice the lemons.');
    expect(note).not.toContain('# Best Lemonade');
  });

  it('adds a photo import label and the model notes', () => {
    const photo: ImportedRecipe = {
      ...RECIPE,
      source_url: '',
      site: 'photo import',
      source_kind: 'photo',
      notes: 'Best with butter.',
    };
    const note = buildRecipeNote(photo, [PHOTO_LABEL]);
    expect(note).toContain('Tags: #level-1 #30-minutes #5-ingredients #photo-import');
    expect(note).toContain('Source: photo import');
    expect(note).toContain('## Notes\nBest with butter.');
  });
});
