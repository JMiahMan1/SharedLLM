import { beforeEach, describe, expect, it, vi } from 'vitest';

const readNote = vi.fn();
const writeNote = vi.fn();
const deleteNote = vi.fn();
vi.mock('../services/api', () => ({ api: { readNote, writeNote, deleteNote } }));

const { readMealPlan, saveMealPlan } = await import('./mealPlanStore');
const { readShoppingList, saveShoppingList } = await import('./shoppingListStore');
const { emptyPlan } = await import('./mealPlan');

const missing = { status: 'FAILURE', message: 'not found' };

beforeEach(() => {
  readNote.mockReset();
  writeNote.mockReset().mockResolvedValue({ status: 'SUCCESS', detail: {} });
  deleteNote.mockReset().mockResolvedValue({ status: 'SUCCESS' });
});

describe('the meal plan and shopping list are saved where they are read', () => {
  it('writes a first plan to "Recipes/Meal plan.md", not a name made from the title', async () => {
    await saveMealPlan(emptyPlan(), false);
    expect(writeNote.mock.calls[0][0].path).toBe('Recipes/Meal plan.md');
  });

  it('writes a first shopping list to "Recipes/Shopping list.md"', async () => {
    await saveShoppingList('- [ ] milk', false);
    expect(writeNote.mock.calls[0][0].path).toBe('Recipes/Shopping list.md');
  });

  it('reads a plan left at the old file name and removes that copy once saved', async () => {
    readNote.mockImplementation(async (_t: string, _s: string, path: string) =>
      path === 'Recipes/Meal-plan.md' ? { status: 'SUCCESS', message: '- [ ] Mon · Chili' } : missing);
    const { plan, exists } = await readMealPlan();
    expect(plan.Mon.title).toBe('Chili');
    expect(exists).toBe(false);
    await saveMealPlan(plan, exists);
    expect(deleteNote.mock.calls[0][2]).toBe('Recipes/Meal-plan.md');
  });

  it('leaves the old copy alone when the plan is already in place', async () => {
    await saveMealPlan(emptyPlan(), true);
    expect(deleteNote).not.toHaveBeenCalled();
  });

  it('reads a shopping list left at the old file name', async () => {
    readNote.mockImplementation(async (_t: string, _s: string, path: string) =>
      path === 'Recipes/Shopping-list.md' ? { status: 'SUCCESS', message: '- [x] eggs' } : missing);
    expect(await readShoppingList()).toEqual({ text: '- [x] eggs', exists: false });
  });
});
