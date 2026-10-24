import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import LogRecipeSheet from './LogRecipeSheet';
import { api } from '../../services/api';
import { localDay } from '../../lib/healthHub';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), addFood: vi.fn() } };
});

const BODY = [
  '## Ingredients',
  '- [ ] beans',
  '',
  '## Nutrition',
  '*Per serving, as stated by the submitter — foodcom:* 400 kcal · 10 g fat · 600 mg sodium · 50 g carbs · 8 g fiber · 4 g sugar · 20 g protein',
].join('\n');

const wrap = (ui: ReactElement) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

describe('LogRecipeSheet', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.addFood).mockResolvedValue({ id: 1, duplicate: false } as never);
  });

  it('logs the stated per-serving values as dinner with the chosen portion', async () => {
    const onClose = vi.fn();
    wrap(<LogRecipeSheet title="Bean Chili" path="Recipes/Bean Chili.md" body={BODY} onClose={onClose} />);

    expect(screen.getByTestId('log-recipe-per-serving')).toHaveTextContent('400 kcal');
    fireEvent.click(screen.getByRole('button', { name: '1½ servings' }));
    expect(screen.getByTestId('log-recipe-totals')).toHaveTextContent('600 kcal · protein 30 g · carbs 75 g · fat 15 g');
    fireEvent.click(screen.getByTestId('log-recipe-submit'));

    await waitFor(() => expect(api.addFood).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.addFood).mock.calls[0][0]).toEqual({
      day: localDay(),
      meal: 'dinner',
      name: 'Bean Chili',
      brand: 'Family recipe',
      calories: 400,
      protein_g: 20,
      carbs_g: 50,
      fat_g: 10,
      fiber_g: 8,
      sugar_g: 4,
      sodium_mg: 600,
      servings: 1.5,
      recipe_path: 'Recipes/Bean Chili.md',
      source: 'recipe',
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('says when a recipe has no nutrition and logs only what is typed', async () => {
    wrap(<LogRecipeSheet title="Toast" path="Recipes/Toast.md" body="## Steps\n1. Toast it" onClose={vi.fn()} />);

    expect(screen.getByTestId('log-recipe-manual')).toHaveTextContent('no per-serving nutrition');
    expect(screen.getByTestId('log-recipe-submit')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Calories (kcal)'), { target: { value: '180' } });
    fireEvent.change(screen.getByLabelText('Protein (g)'), { target: { value: '6' } });
    fireEvent.change(screen.getByLabelText('Meal'), { target: { value: 'breakfast' } });
    fireEvent.click(screen.getByTestId('log-recipe-submit'));

    await waitFor(() => expect(api.addFood).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.addFood).mock.calls[0][0]).toMatchObject({
      meal: 'breakfast',
      calories: 180,
      protein_g: 6,
      servings: 1,
      recipe_path: 'Recipes/Toast.md',
      source: 'manual',
    });
  });

  it('names what is missing from an incomplete line', () => {
    wrap(
      <LogRecipeSheet
        title="Soup"
        path="Recipes/Soup.md"
        body={'## Nutrition\n*Per serving — foodcom:* 200 kcal · 4 g fat'}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId('log-recipe-manual')).toHaveTextContent('missing protein, carbs');
  });
});
