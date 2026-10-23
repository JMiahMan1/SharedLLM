import { describe, expect, it } from 'vitest';
import { buildShoppingList, ingredientLines, parseShoppingList } from '../lib/shoppingList';

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
});
