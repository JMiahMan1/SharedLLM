import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import MealsCard from './MealsCard';
import { api } from '../../services/api';
import { planDayOf } from '../../lib/mealPlan';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), readNote: vi.fn() } };
});

const wrap = (ui: ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );

const PLAN = `# Meal plan\nCategory: Recipes\n\n#meal-plan\n\n- [ ] ${planDayOf()} · Chicken Chili\n- [ ] Tue ·\n`;

const RECIPE = [
  'Tags: #level-2',
  'Times: prep 15 min · cook 30 min · total 45 min · servings 7 (scaled from 4)',
  '',
  '## Ingredients',
  '- [ ] 2 lb beef',
  '',
  '## Nutrition',
  '*Per serving, as stated by the submitter - foodcom:* 350.2 kcal · 12 g fat · 30 g carbs · 25 g protein',
].join('\n');

function mockNotes(recipeBody: string | null) {
  vi.mocked(api.readNote).mockImplementation(async (title: string) => {
    if (title === 'Meal plan') return { status: 'SUCCESS', message: PLAN, service: 'note_read' } as never;
    if (recipeBody === null) return { status: 'FAILURE', message: 'not found', service: 'note_read' } as never;
    return { status: 'SUCCESS', message: recipeBody, service: 'note_read' } as never;
  });
}

describe('MealsCard', () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows tonight's dinner with its per-serving and whole-recipe nutrition", async () => {
    mockNotes(RECIPE);
    wrap(<MealsCard />);
    expect(await screen.findByTestId('today-meals-title')).toHaveTextContent('Chicken Chili');
    const line = await screen.findByTestId('today-meals-nutrition');
    expect(line).toHaveTextContent('350 kcal a serving');
    expect(line).toHaveTextContent('whole recipe (7 servings): 2451 kcal');
    expect(screen.getByTestId('today-meals-log')).toBeEnabled();
  });

  it('says so when the recipe states no nutrition, never a number', async () => {
    mockNotes('## Ingredients\n- [ ] eggs');
    wrap(<MealsCard />);
    expect(await screen.findByTestId('today-meals-no-nutrition')).toBeInTheDocument();
    expect(screen.queryByTestId('today-meals-nutrition')).toBeNull();
  });

  it('hides itself when nothing is planned', async () => {
    vi.mocked(api.readNote).mockResolvedValue({ status: 'FAILURE', message: 'not found', service: 'note_read' } as never);
    const { container } = wrap(<MealsCard />);
    await waitFor(() => expect(container.querySelector('[data-testid="today-meals-card"]')).toBeNull());
  });

  it('hides the nutrition line in kid mode', async () => {
    mockNotes(RECIPE);
    wrap(<MealsCard kid />);
    expect(await screen.findByTestId('today-meals-title')).toHaveTextContent('Chicken Chili');
    expect(screen.queryByTestId('today-meals-nutrition')).toBeNull();
  });
});
