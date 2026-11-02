import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import Recipes, { RECIPES_DIR } from './Recipes';
import { api } from '../services/api';
import type { CookbookRecipe } from '../types/api';
import { PLAN_DAYS, planDayOf } from '../lib/mealPlan';

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn(),
      getHealthProfile: vi.fn(),
      recipeCatalog: vi.fn(),
      cookbookRead: vi.fn(),
      cookbookSuggestions: vi.fn(),
      cookbookPersonal: vi.fn(),
      recipeMade: vi.fn(),
      recipeUnmade: vi.fn(),
      readNote: vi.fn(),
      writeNote: vi.fn(),
      deleteNote: vi.fn(),
      addFood: vi.fn(),
      getTalkConversations: vi.fn(),
      sendTalkMessage: vi.fn(),
      getSkylightMeals: vi.fn(),
      pushSkylightWeek: vi.fn(),
      mirrorSkylightList: vi.fn(),
      mealPlanIcs: vi.fn(),
    },
  };
});

vi.mock('../components/chat/familyDirectory', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../components/chat/familyDirectory')>()),
  useFamilyDirectory: () => () => undefined,
}));

const m = vi.mocked(api);

function recipe(title: string, extra: Partial<CookbookRecipe> = {}): CookbookRecipe {
  return {
    title, path: `${RECIPES_DIR}/${title.replace(/ /g, '-')}.md`, tags: [], meal: 'main', proteins: ['vegetarian'],
    total_minutes: 30, servings: 4, calories: null, ingredients: 2, level: null, owner: 'mom', owner_label: 'Mom',
    can_change: true, source: '', image: null, staples: [], rating: null, ratings: 0, my_rating: null, made: 0,
    made_by_me: 0, last_made: null, ...extra,
  };
}

const BODIES: Record<string, string> = {
  'Chicken Chili': '## Ingredients\n- [ ] 1 tsp cumin\n- [ ] 2 cups beans',
  Pancakes: '## Ingredients\n- [ ] 2 cups flour\n\n## Nutrition\n*Per serving - foodcom:* 300 kcal · 9 g fat · 45 g carbs · 8 g protein',
};

let planText = '#meal-plan\n\n- [ ] Mon · Chicken Chili';
let listText: string | null = null;

function renderWeek(tab = 'week') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/recipes?tab=${tab}`]}>
        <Recipes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Recipes week plan', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    planText = '#meal-plan\n\n- [ ] Mon · Chicken Chili';
    listText = null;
    m.getMe.mockResolvedValue({ username: 'mom', is_admin: false } as never);
    m.getHealthProfile.mockResolvedValue({ kid_mode: false } as never);
    m.recipeCatalog.mockResolvedValue({
      recipes: [recipe('Chicken Chili', { meal: 'soup' }), recipe('Pancakes'), recipe('Tacos'), recipe('Soup', { meal: 'soup' }), recipe('Pasta'), recipe('Curry'), recipe('Stew', { meal: 'soup' })],
      me: 'mom',
      is_admin: false,
    });
    m.cookbookRead.mockImplementation((async ({ path, title }: { path?: string; title?: string }) => {
      const name = title ?? Object.keys(BODIES).find((t) => path?.includes(t.replace(/ /g, '-'))) ?? '';
      return { status: 'SUCCESS', message: BODIES[name] ?? '', service: 'cookbook', detail: { path: path ?? `${RECIPES_DIR}/${name}.md` } };
    }) as never);
    m.cookbookSuggestions.mockResolvedValue([]);
    m.cookbookPersonal.mockResolvedValue([]);
    m.recipeMade.mockResolvedValue({} as never);
    m.readNote.mockImplementation((async (title: string) => {
      if (title === 'Meal plan') return { status: 'SUCCESS', message: planText, service: 'note_read' };
      if (title === 'Shopping list' && listText !== null) return { status: 'SUCCESS', message: listText, service: 'note_read' };
      return { status: 'FAILURE', message: 'Not found', service: 'note_read' };
    }) as never);
    m.writeNote.mockResolvedValue({ status: 'SUCCESS', message: '', service: 'note_write' } as never);
    m.deleteNote.mockResolvedValue({ status: 'SUCCESS', message: '', service: 'note_delete' } as never);
    m.getTalkConversations.mockResolvedValue({
      status: 'SUCCESS', message: '', service: 'talk_conversations',
      detail: { conversations: [{ token: 'room-1', display_name: 'Family' }] },
    } as never);
    m.sendTalkMessage.mockResolvedValue({ status: 'SUCCESS', message: '', service: 'talk_send' } as never);
    m.getSkylightMeals.mockResolvedValue({ status: 'SUCCESS', configured: true, categories: [] } as never);
    m.pushSkylightWeek.mockResolvedValue({ status: 'SUCCESS', planned: [], failed: [] } as never);
    m.mirrorSkylightList.mockResolvedValue({ status: 'SUCCESS', added: 0, removed: 0 } as never);
    // jsdom has no object-URL support; the .ics download just needs a URL string.
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: vi.fn(() => 'blob:meal-plan') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() });
  });

  const ready = () => waitFor(() => expect(screen.getByTestId('plan-day-Mon')).toHaveTextContent('Chicken Chili'));

  it('marks a day cooked and saves the plan as it changes', async () => {
    renderWeek();
    await ready();
    fireEvent.click(screen.getByLabelText('Mon cooked'));
    await waitFor(() => expect(m.writeNote).toHaveBeenCalled());
    const body = m.writeNote.mock.calls[0][0];
    expect(body.title).toBe('Meal plan');
    expect(body.category).toBe(RECIPES_DIR);
    expect(body.content).toContain('- [x] Mon · Chicken Chili');
  });

  it('fills the empty days with surprise picks', async () => {
    renderWeek();
    await ready();
    await waitFor(() => expect(screen.getByLabelText('Plan for Sun')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /fill the gaps/i }));
    await waitFor(() => expect(m.writeNote).toHaveBeenCalled());
    const content = m.writeNote.mock.calls[0][0].content;
    for (const day of PLAN_DAYS) expect(content).toMatch(new RegExp(`${day} · \\S`));
    expect(content).toContain('Mon · Chicken Chili');
  });

  it('builds the shopping list from the week, leaving cooked dinners off', async () => {
    planText = '#meal-plan\n\n- [x] Mon · Chicken Chili\n- [ ] Tue · Pancakes';
    renderWeek();
    await waitFor(() => expect(screen.getByTestId('plan-day-Tue')).toHaveTextContent('Pancakes'));
    fireEvent.click(screen.getByRole('button', { name: /build shopping list/i }));
    await waitFor(() => expect(m.writeNote).toHaveBeenCalled());
    const body = m.writeNote.mock.calls[0][0];
    expect(body.title).toBe('Shopping list');
    expect(body.content).toContain('#shopping-list');
    expect(body.content).toContain('- [ ] 2 cups flour');
    expect(body.content).not.toContain('cumin');
  });

  it('shares the week plan to a family chat', async () => {
    renderWeek();
    await ready();
    fireEvent.click(screen.getByRole('button', { name: /^share$/i }));
    fireEvent.click(await screen.findByTestId('bible-share-chat-target'));
    await waitFor(() => expect(m.sendTalkMessage).toHaveBeenCalled());
    const sent = m.sendTalkMessage.mock.calls[0][0];
    expect(sent.token).toBe('room-1');
    expect(sent.message).toContain('Mon · Chicken Chili');
  });

  it("logs tonight's dinner, and ticks it cooked in the saved plan", async () => {
    const today = planDayOf();
    const tomorrow = PLAN_DAYS[(PLAN_DAYS.indexOf(today) + 1) % 7];
    planText = `#meal-plan\n\n- [ ] ${today} · Pancakes\n- [ ] ${tomorrow} · Pancakes`;
    m.addFood.mockResolvedValue({} as never);
    renderWeek();
    // Only today's card offers it.
    expect(await screen.findAllByTestId('plan-log-today')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('plan-log-today'));
    expect(await screen.findByTestId('log-recipe-sheet')).toBeTruthy();
    expect(screen.getByTestId('log-recipe-per-serving')).toHaveTextContent('300 kcal');
    expect(screen.getByTestId('log-recipe-mark-cooked')).toBeChecked();
    fireEvent.click(screen.getByTestId('log-recipe-submit'));
    await waitFor(() => expect(m.writeNote).toHaveBeenCalledWith(expect.objectContaining({ title: 'Meal plan', content: expect.stringContaining(`- [x] ${today} · Pancakes`) })));
  });

  it('pushes the week to Skylight, one dated sitting per planned dinner', async () => {
    m.pushSkylightWeek.mockResolvedValue({ status: 'SUCCESS', planned: [{ date: '2026-10-26', title: 'Chicken Chili' }], failed: [] } as never);
    renderWeek();
    await ready();
    fireEvent.click(screen.getByTestId('plan-send-skylight'));
    await waitFor(() => expect(m.pushSkylightWeek).toHaveBeenCalled());
    const sent = m.pushSkylightWeek.mock.calls[0][0];
    expect(sent.days).toHaveLength(1);
    expect(sent.days[0].title).toBe('Chicken Chili');
    expect(sent.days[0].date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(sent.days[0].body).toContain('cumin');
  });

  it('mirrors what is left on the shopping list onto the Skylight grocery list', async () => {
    listText = '#shopping-list\n\n- [ ] 2 cups flour\n- [x] salt';
    renderWeek('list');
    expect(await screen.findByText('2 cups flour')).toBeTruthy();
    fireEvent.click(screen.getByTestId('list-send-skylight'));
    await waitFor(() => expect(m.mirrorSkylightList).toHaveBeenCalledWith({ items: ['2 cups flour'] }));
  });

  it('does not push the week when Skylight is not connected', async () => {
    m.getSkylightMeals.mockResolvedValue({ status: 'SUCCESS', configured: false, categories: [] } as never);
    renderWeek();
    await ready();
    await waitFor(() => expect(m.getSkylightMeals).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(screen.getByTestId('plan-send-skylight'));
    expect(m.pushSkylightWeek).not.toHaveBeenCalled();
  });

  it('publishes the week as a calendar file', async () => {
    m.mealPlanIcs.mockResolvedValue({ ics: 'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n', events: 1 } as never);
    renderWeek();
    await ready();
    fireEvent.click(screen.getByTestId('plan-ics'));
    await waitFor(() => expect(m.mealPlanIcs).toHaveBeenCalledWith(undefined));
    expect(URL.createObjectURL).toHaveBeenCalled();
  });

  it('will not publish an empty week', async () => {
    planText = '#meal-plan\n\n';
    renderWeek();
    await screen.findByTestId('meal-plan');
    expect(screen.getByTestId('plan-ics')).toBeDisabled();
  });

  it('opens a planned recipe from its day', async () => {
    renderWeek();
    await ready();
    fireEvent.click(within(screen.getByTestId('plan-day-Mon')).getByText('Chicken Chili'));
    expect(await screen.findByTestId('recipe-view')).toBeTruthy();
  });
});
