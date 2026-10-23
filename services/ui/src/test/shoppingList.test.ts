import { describe, expect, it } from 'vitest';
import { buildShoppingList, ingredientLines, parseShoppingList, shoppingShareText } from '../lib/shoppingList';

const RECIPE = `## Ingredients
- [ ] 2 cups flour
- [ ] 1 tsp salt
- [x] 3 eggs

## Steps
1. Mix everything.`;

describe('shoppingList', () => {
  it('collects ingredient checklist lines and skips the steps', () => {
    expect(ingredientLines(RECIPE)).toEqual(['2 cups flour', '1 tsp salt', '3 eggs']);
  });

  it('dedupes by normalized text and starts with the tag', () => {
    const body = buildShoppingList('', ['2 cups flour', '1 tsp salt', '2 cups flour']);
    expect(body.startsWith('#shopping-list')).toBe(true);
    expect(parseShoppingList(body).map((i) => i.text)).toEqual(['2 cups flour', '1 tsp salt']);
    expect(body).toContain('- [ ] 2 cups flour');
  });

  it('keeps ticks by item text when regenerating', () => {
    const previous = '#shopping-list\n\n- [x] 2 cups flour\n- [ ] salt';
    const body = buildShoppingList(previous, ['2 Cups Flour', 'salt', 'pepper']);
    expect(body).toContain('- [x] 2 Cups Flour');
    expect(body).toContain('- [ ] salt');
    expect(body).toContain('- [ ] pepper');
  });

  it('keeps ticked items that are not in the new list', () => {
    const previous = '#shopping-list\n\n- [x] paper towels';
    const body = buildShoppingList(previous, ['2 cups flour']);
    const rows = parseShoppingList(body);
    expect(rows).toEqual([
      { text: '2 cups flour', checked: false },
      { text: 'paper towels', checked: true },
    ]);
    expect(body).toContain("Made from the week's plan");
  });

  it('says there is nothing to buy when nothing was planned', () => {
    const body = buildShoppingList('', []);
    expect(body).toContain('Nothing to buy yet');
    expect(parseShoppingList(body)).toEqual([]);
  });

  it('writes a short shareable list with an optional note', () => {
    const body = buildShoppingList('', ['2 cups flour', '1 tsp salt']);
    const text = shoppingShareText(body, 'shopping run?');
    expect(text.startsWith('shopping run?\n\n')).toBe(true);
    expect(text).toContain('🛒 Shopping list:');
    expect(text).toContain('- [ ] 2 cups flour');
    expect(shoppingShareText('')).toBe('🛒 The shopping list is empty.');
  });

  it('caps very long lists at 25 items', () => {
    const many = Array.from({ length: 30 }, (_, index) => `item ${index + 1}`);
    const body = buildShoppingList('', many);
    const text = shoppingShareText(body);
    expect(text).toContain('- [ ] item 25');
    expect(text).not.toContain('- [ ] item 26');
    expect(text).toContain('…and 5 more.');
  });
});
