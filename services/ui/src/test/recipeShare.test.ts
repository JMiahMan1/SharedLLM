import { describe, it, expect } from 'vitest';
import { recipeBodyText, recipeSharePreview, recipeShareText } from '../lib/recipeShare';

const STORED =
  '# Pancakes\n' +
  'Category: Recipes\n' +
  'Tags: #level-1 #30-minutes\n' +
  'Times: prep 5 min · total 15 min · servings 7 (scaled from 4)\n' +
  'Source: https://www.food.com/recipe/pancakes-123 - Food.com, CC0 dataset\n' +
  '\n' +
  '## Ingredients\n' +
  '- [ ] 2 cups flour';

describe('recipeShareText', () => {
  it('strips the stored header down to the body', () => {
    expect(recipeBodyText(STORED)).toBe('## Ingredients\n- [ ] 2 cups flour');
  });

  it('keeps a plain body untouched', () => {
    expect(recipeBodyText('flour, milk')).toBe('flour, milk');
  });

  it('puts the note first and excerpts with a title', () => {
    expect(recipeShareText('Pancakes', STORED, 'breakfast vote?')).toBe(
      'breakfast vote?\n\n🍽 Pancakes: ## Ingredients - [ ] 2 cups flour',
    );
  });

  it('trims a long excerpt to a word boundary', () => {
    const long = 'word '.repeat(80);
    const preview = recipeSharePreview(long);
    expect(preview.length).toBeLessThanOrEqual(221);
    expect(preview.endsWith('…')).toBe(true);
    expect(preview.includes('  ')).toBe(false);
  });
});
