import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import MealsCard from './MealsCard';
import { api } from '../../services/api';
import { planDayOf } from '../../lib/mealPlan';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      readNote: vi.fn(),
      getFoodEntries: vi.fn(),
    },
  };
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

function mockFood(entries: Array<Record<string, unknown>>) {
  vi.mocked(api.getFoodEntries).mockResolvedValue({ user_id: 'dad', entries } as never);
}

function dinnerEntry(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    user_id: 'dad',
    day: '2026-10-22',
    meal: 'dinner',
    name: 'Chicken Chili',
    brand: 'Family recipe',
    calories: 350,
    protein_g: 25,
    carbs_g: 30,
    fat_g: 12,
    fiber_g: null,
    sugar_g: null,
    sodium_mg: null,
    servings: 1,
    serving_grams: null,
    barcode: null,
    recipe_path: 'Recipes/Chicken Chili.md',
    source: 'recipe',
    external_id: null,
    created_at: '',
    density: null,
    ...overrides,
  };
}

describe('MealsCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFood([]);
  });

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

  it('shows the planned dinner logged as itself', async () => {
    mockNotes(RECIPE);
    mockFood([dinnerEntry()]);
    wrap(<MealsCard />);
    const match = await screen.findByTestId('today-meals-eaten-match');
    expect(match).toHaveTextContent('Chicken Chili');
    expect(match).toHaveTextContent('1 serving');
    expect(match).toHaveTextContent('350 kcal');
    expect(screen.queryByTestId('today-meals-eaten-other')).toBeNull();
  });

  it('says when something else was logged for dinner instead', async () => {
    mockNotes(RECIPE);
    mockFood([dinnerEntry({ id: 2, name: 'Pizza', brand: '', recipe_path: null, calories: 800 })]);
    wrap(<MealsCard />);
    const other = await screen.findByTestId('today-meals-eaten-other');
    expect(other).toHaveTextContent('Pizza');
    expect(other).toHaveTextContent("not tonight's plan");
  });

  it('says nothing is logged for dinner yet', async () => {
    mockNotes(RECIPE);
    mockFood([]);
    wrap(<MealsCard />);
    expect(await screen.findByTestId('today-meals-eaten-none')).toHaveTextContent('Nothing logged for dinner yet');
  });

  it('hides the logged calories in kid mode', async () => {
    mockNotes(RECIPE);
    mockFood([dinnerEntry()]);
    wrap(<MealsCard kid />);
    const match = await screen.findByTestId('today-meals-eaten-match');
    expect(match).toHaveTextContent('Chicken Chili');
    expect(match).not.toHaveTextContent('kcal');
  });
});
