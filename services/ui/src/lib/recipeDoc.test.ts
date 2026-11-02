import { describe, expect, it } from 'vitest';
import { buildRecipeDoc, parseRecipeDoc } from './recipeDoc';

const NOTE = `Tags: #dinner #level-2
Times: prep 15 min · cook 30 min · servings 6
Source: https://www.food.com/recipe/chili - imported from food.com
Image: .attachments.4/photo.jpg
Added by: mom

## Ingredients
- [ ] 2 lb chicken
- [ ] 1 onion

## Steps
1. Brown the chicken.
2. Simmer.

## Notes
Better the next day.

## Nutrition
*Per serving, as stated by the submitter - foodcom:* 337.8 kcal · 9.1 g fat`;

describe('recipe notes as fields', () => {
  it('reads every part of a note', () => {
    const doc = parseRecipeDoc(NOTE);
    expect(doc.tags).toEqual(['dinner', 'level-2']);
    expect([doc.prepMinutes, doc.cookMinutes, doc.totalMinutes, doc.servings]).toEqual([15, 30, 45, 6]);
    expect(doc.ingredients).toEqual(['2 lb chicken', '1 onion']);
    expect(doc.steps).toEqual(['Brown the chicken.', 'Simmer.']);
    expect(doc.notes).toBe('Better the next day.');
    expect(doc.nutrition).toContain('337.8 kcal');
    expect(doc.intro).toBe('');
  });

  it('writes the fields back in the same shape, without the server-kept lines', () => {
    const again = buildRecipeDoc(parseRecipeDoc(NOTE));
    expect(again).toContain('Times: prep 15 min · cook 30 min · servings 6');
    expect(again).toContain('- [ ] 2 lb chicken');
    expect(again).toContain('2. Simmer.');
    expect(again).toContain('## Nutrition');
    expect(again).not.toContain('Added by');
    expect(parseRecipeDoc(again)).toEqual(parseRecipeDoc(NOTE));
  });

  it('keeps a section it does not know', () => {
    const doc = parseRecipeDoc('## Ingredients\n- a\n\n## Sauce\nMix it.\n');
    expect(buildRecipeDoc(doc)).toContain('## Sauce\nMix it.');
  });
});
