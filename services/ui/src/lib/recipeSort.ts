import type { CookbookRecipe } from '../types/api';

export type RecipeSort = 'top' | 'made' | 'staples' | 'quick' | 'new' | 'az';

function modifiedTime(recipe: CookbookRecipe): number {
  const t = recipe.modified ? Date.parse(recipe.modified) : NaN;
  return Number.isFinite(t) ? t : 0;
}

export function sortRecipes(recipes: CookbookRecipe[], sort: RecipeSort): CookbookRecipe[] {
  const byTitle = (a: CookbookRecipe, b: CookbookRecipe) => a.title.localeCompare(b.title);
  const out = [...recipes];
  out.sort((a, b) => {
    switch (sort) {
      case 'top':
        return (b.rating ?? -1) - (a.rating ?? -1) || b.ratings - a.ratings || b.made - a.made || byTitle(a, b);
      case 'made':
        return b.made - a.made || (b.rating ?? -1) - (a.rating ?? -1) || byTitle(a, b);
      case 'staples':
        return b.staples.length - a.staples.length || byTitle(a, b);
      case 'quick':
        return (a.total_minutes ?? Infinity) - (b.total_minutes ?? Infinity) || byTitle(a, b);
      case 'new':
        return modifiedTime(b) - modifiedTime(a) || byTitle(a, b);
      default:
        return byTitle(a, b);
    }
  });
  return out;
}
