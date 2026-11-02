import { describe, expect, it } from 'vitest';
import { formatAmount, parseAmount, scaleIngredient } from './recipeScale';

describe('recipe scaling', () => {
  it('reads the amounts cooks write', () => {
    expect(parseAmount('1 1/2')).toBe(1.5);
    expect(parseAmount('3/4')).toBe(0.75);
    expect(parseAmount('1½')).toBe(1.5);
    expect(parseAmount('½')).toBe(0.5);
    expect(parseAmount('2.5')).toBe(2.5);
  });

  it('writes measuring-cup fractions back', () => {
    expect(formatAmount(1.5)).toBe('1½');
    expect(formatAmount(1 / 3)).toBe('⅓');
    expect(formatAmount(0.66)).toBe('⅔');
    expect(formatAmount(3)).toBe('3');
    expect(formatAmount(0.99)).toBe('1');
    expect(formatAmount(24.4)).toBe('24');
  });

  it('scales the leading amount and ranges, and leaves the rest', () => {
    expect(scaleIngredient('1 1/2 cups flour', 2)).toBe('3 cups flour');
    expect(scaleIngredient('2-3 cloves garlic', 2)).toBe('4-6 cloves garlic');
    expect(scaleIngredient('½ tsp salt', 3)).toBe('1½ tsp salt');
    expect(scaleIngredient('3 eggs', 0.5)).toBe('1½ eggs');
    expect(scaleIngredient('salt to taste', 2)).toBe('salt to taste');
    expect(scaleIngredient('2 lb chicken', 1)).toBe('2 lb chicken');
  });
});
