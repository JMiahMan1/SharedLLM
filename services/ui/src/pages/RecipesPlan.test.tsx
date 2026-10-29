import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import Recipes, { RECIPES_DIR } from './Recipes';
import { api } from '../services/api';
import { PLAN_DAYS, planDayOf } from '../lib/mealPlan';

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn(),
      getHealthProfile: vi.fn(),
      listNotes: vi.fn(),
      readNote: vi.fn(),
      writeNote: vi.fn(),
      deleteNote: vi.fn(),
      syncNotesRag: vi.fn(),
      getTalkConversations: vi.fn(),
      sendTalkMessage: vi.fn(),
      addFood: vi.fn(),
      getSkylightMeals: vi.fn(),
      sendSkylightRecipe: vi.fn(),
      pushSkylightWeek: vi.fn(),
      mirrorSkylightList: vi.fn(),
    },
  };
});

vi.mock('../components/chat/familyDirectory', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../components/chat/familyDirectory')>()),
  useFamilyDirectory: () => () => undefined,
}));

const getMe = vi.mocked(api.getMe);
const listNotes = vi.mocked(api.listNotes);
const readNote = vi.mocked(api.readNote);
const writeNote = vi.mocked(api.writeNote);
const getTalkConversations = vi.mocked(api.getTalkConversations);
const sendTalkMessage = vi.mocked(api.sendTalkMessage);
const getSkylightMeals = vi.mocked(api.getSkylightMeals);
const pushSkylightWeek = vi.mocked(api.pushSkylightWeek);
const mirrorSkylightList = vi.mocked(api.mirrorSkylightList);

function listResponse(titles: string[]) {
  return {
    status: 'SUCCESS' as const,
    message: '',
    service: 'note_list',
    detail: { notes: titles.map((title) => ({ title, path: `${RECIPES_DIR}/${title}.md` })) },
  };
}

function renderRecipes() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Recipes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const PLAN_RESPONSE = {
  status: 'SUCCESS' as const,
  message: '#meal-plan\n\n- [ ] Mon · Chicken Chili',
  service: 'note_read',
};

const RECIPE_RESPONSE = {
  status: 'SUCCESS' as const,
  message: '# Pancakes\nCategory: Recipes\nTags: #level-2\n\nflour, milk',
  service: 'note_read',
};

describe('Recipes week plan', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getMe.mockResolvedValue({ username: 'mom', is_admin: false } as never);
    vi.mocked(api.getHealthProfile).mockResolvedValue({ kid_mode: false } as never);
    listNotes.mockResolvedValue(listResponse(['Pancakes']) as never);
    readNote.mockImplementation(
      (async (title: string) => (title === 'Meal plan' ? PLAN_RESPONSE : RECIPE_RESPONSE)) as never,
    );
    writeNote.mockResolvedValue({ status: 'SUCCESS', message: '', service: 'note_write' } as never);
    getTalkConversations.mockResolvedValue({
      status: 'SUCCESS',
      message: '',
      service: 'talk_conversations',
      detail: { conversations: [{ token: 'room-1', display_name: 'Family' }] },
    } as never);
    sendTalkMessage.mockResolvedValue({ status: 'SUCCESS', message: '', service: 'talk_send' } as never);
    getSkylightMeals.mockResolvedValue({ status: 'SUCCESS', configured: true, categories: [] } as never);
    pushSkylightWeek.mockResolvedValue({ status: 'SUCCESS', planned: [], failed: [] } as never);
    mirrorSkylightList.mockResolvedValue({ status: 'SUCCESS', added: 0, removed: 0 } as never);
  });

  it('picks a day, marks it cooked, and saves the plan', async () => {
    renderRecipes();

    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));
    expect(await screen.findByTestId('meal-plan')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Plan for Mon'), { target: { value: 'Pancakes' } });
    expect(await screen.findByText(/Level 2/)).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Mon cooked'));
    fireEvent.click(screen.getByRole('button', { name: /save plan/i }));

    await waitFor(() => expect(writeNote).toHaveBeenCalled());
    const body = writeNote.mock.calls[0][0];
    expect(body.title).toBe('Meal plan');
    expect(body.category).toBe(RECIPES_DIR);
    expect(body.content).toContain('#meal-plan');
    expect(body.content).toContain('- [x] Mon · Pancakes');
  });

  it('fills the week with surprise picks', async () => {
    listNotes.mockResolvedValue(
      listResponse(['Pancakes', 'Chili', 'Tacos', 'Soup', 'Pasta', 'Salad', 'Curry']) as never,
    );
    readNote.mockImplementation(
      (async (title: string) =>
        title === 'Meal plan'
          ? { status: 'SUCCESS' as const, message: '#meal-plan\n\n', service: 'note_read' }
          : RECIPE_RESPONSE) as never,
    );
    renderRecipes();

    // Wait for the recipe list before filling the week.
    await screen.findByText('Pancakes');
    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));
    await screen.findByTestId('meal-plan');

    fireEvent.click(screen.getByRole('button', { name: /surprise me/i }));

    await waitFor(() => {
      const monday = screen.getByLabelText('Plan for Mon') as HTMLSelectElement;
      expect(monday.value).not.toBe('');
    });
    const sunday = screen.getByLabelText('Plan for Sun') as HTMLSelectElement;
    expect(sunday.value).not.toBe('');
  });

  it('builds the shopping list from the week', async () => {
    readNote.mockImplementation(
      (async (title: string) => {
        if (title === 'Meal plan') return PLAN_RESPONSE;
        if (title === 'Chicken Chili') {
          return {
            status: 'SUCCESS' as const,
            message:
              '# Chicken Chili\nCategory: Recipes\n\n## Ingredients\n- [ ] 2 cups flour\n- [ ] 1 tsp salt',
            service: 'note_read',
          };
        }
        if (title === 'Shopping list') {
          return { status: 'FAILURE' as const, message: 'Not found', service: 'note_read' };
        }
        return RECIPE_RESPONSE;
      }) as never,
    );
    renderRecipes();

    // Wait for the recipe list before opening the plan.
    await screen.findByText('Pancakes');
    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));
    await screen.findByTestId('meal-plan');

    fireEvent.click(screen.getByRole('button', { name: /build shopping list/i }));

    await waitFor(() => expect(writeNote).toHaveBeenCalled());
    const body = writeNote.mock.calls[0][0];
    expect(body.title).toBe('Shopping list');
    expect(body.category).toBe(RECIPES_DIR);
    expect(body.content).toContain('#shopping-list');
    expect(body.content).toContain('- [ ] 2 cups flour');
  });

  it('shares the week plan to a family chat', async () => {
    renderRecipes();

    // Wait for the recipe list before opening the plan.
    await screen.findByText('Pancakes');
    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));
    await screen.findByTestId('meal-plan');

    fireEvent.click(screen.getByRole('button', { name: /share plan/i }));

    fireEvent.click(await screen.findByTestId('bible-share-chat-target'));

    await waitFor(() => expect(sendTalkMessage).toHaveBeenCalled());
    const sent = sendTalkMessage.mock.calls[0][0];
    expect(sent.token).toBe('room-1');
    expect(sent.message).toContain("This week's dinners");
    expect(sent.message).toContain('Mon · Chicken Chili');
  });

  it("logs tonight's planned dinner from the recipe note", async () => {
    const today = planDayOf();
    const tomorrow = PLAN_DAYS[(PLAN_DAYS.indexOf(today) + 1) % 7];
    readNote.mockImplementation(
      (async (title: string) => {
        if (title === 'Meal plan') {
          return {
            status: 'SUCCESS' as const,
            message: `#meal-plan\n\n- [ ] ${today} · Pancakes\n- [ ] ${tomorrow} · Pancakes`,
            service: 'note_read',
          };
        }
        return {
          status: 'SUCCESS' as const,
          message: '# Pancakes\nCategory: Recipes\n\n## Nutrition\n*Per serving - foodcom:* 300 kcal · 9 g fat · 45 g carbs · 8 g protein',
          service: 'note_read',
        };
      }) as never,
    );
    renderRecipes();
    await screen.findByText('Pancakes');
    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));

    // Only today's row offers it.
    expect(await screen.findAllByTestId('plan-log-today')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('plan-log-today'));
    expect(await screen.findByTestId('log-recipe-sheet')).toBeTruthy();
    expect(screen.getByTestId('log-recipe-per-serving')).toHaveTextContent('300 kcal');
    expect(readNote).toHaveBeenCalledWith('Pancakes', 'nextcloud', `${RECIPES_DIR}/Pancakes.md`, undefined);
  });

  it("ticks tonight's dinner cooked in the saved plan once it is logged", async () => {
    const today = planDayOf();
    readNote.mockImplementation(
      (async (title: string) => {
        if (title === 'Meal plan') {
          return { status: 'SUCCESS' as const, message: `#meal-plan\n\n- [ ] ${today} · Pancakes`, service: 'note_read' };
        }
        return {
          status: 'SUCCESS' as const,
          message: '# Pancakes\nCategory: Recipes\n\n## Nutrition\n*Per serving - foodcom:* 300 kcal · 9 g fat · 45 g carbs · 8 g protein',
          service: 'note_read',
        };
      }) as never,
    );
    vi.mocked(api.addFood).mockResolvedValue({} as never);
    renderRecipes();
    await screen.findByText('Pancakes');
    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));
    fireEvent.click(await screen.findByTestId('plan-log-today'));
    expect(await screen.findByTestId('log-recipe-mark-cooked')).toBeChecked();
    fireEvent.click(screen.getByTestId('log-recipe-submit'));

    await waitFor(() => expect(writeNote).toHaveBeenCalled());
    const saved = writeNote.mock.calls[0][0];
    expect(saved.title).toBe('Meal plan');
    expect(saved.content).toContain(`- [x] ${today} · Pancakes`);
    expect(await screen.findByLabelText(`${today} cooked`)).toBeChecked();
  });

  it('leaves a cooked dinner off the shopping list', async () => {
    readNote.mockImplementation(
      (async (title: string) => {
        if (title === 'Meal plan') {
          return { status: 'SUCCESS' as const, message: '#meal-plan\n\n- [x] Mon · Chicken Chili\n- [ ] Tue · Pancakes', service: 'note_read' };
        }
        if (title === 'Chicken Chili') {
          return { status: 'SUCCESS' as const, message: '# Chicken Chili\n\n- [ ] 1 tsp cumin', service: 'note_read' };
        }
        if (title === 'Shopping list') return { status: 'FAILURE' as const, message: 'Not found', service: 'note_read' };
        return { status: 'SUCCESS' as const, message: '# Pancakes\n\n- [ ] 2 cups flour', service: 'note_read' };
      }) as never,
    );
    renderRecipes();
    await screen.findByText('Pancakes');
    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));
    await screen.findByTestId('meal-plan');
    fireEvent.click(screen.getByRole('button', { name: /build shopping list/i }));

    await waitFor(() => expect(writeNote).toHaveBeenCalled());
    const body = writeNote.mock.calls[0][0].content;
    expect(body).toContain('2 cups flour');
    expect(body).not.toContain('cumin');
  });

  it('offers Log to Health from an open recipe', async () => {
    renderRecipes();
    fireEvent.click(await screen.findByText('Pancakes'));
    fireEvent.click(await screen.findByTestId('recipe-log-health'));
    // The test recipe has no nutrition: the sheet says so instead of guessing.
    expect(await screen.findByTestId('log-recipe-manual')).toHaveTextContent('no per-serving nutrition');
  });

  it('pushes the week to Skylight, one dated sitting per planned dinner', async () => {
    readNote.mockImplementation(
      (async (title: string) => {
        if (title === 'Meal plan') return PLAN_RESPONSE;
        if (title === 'Chicken Chili') {
          return { status: 'SUCCESS' as const, message: '# Chicken Chili\n\n- [ ] 1 tsp cumin', service: 'note_read' };
        }
        return RECIPE_RESPONSE;
      }) as never,
    );
    pushSkylightWeek.mockResolvedValue({ status: 'SUCCESS', planned: [{ date: '2026-10-26', title: 'Chicken Chili' }], failed: [] } as never);
    renderRecipes();
    await screen.findByText('Pancakes');
    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));
    await screen.findByTestId('meal-plan');

    fireEvent.click(screen.getByTestId('plan-send-skylight'));

    await waitFor(() => expect(pushSkylightWeek).toHaveBeenCalled());
    const sent = pushSkylightWeek.mock.calls[0][0];
    expect(sent.days).toHaveLength(1);
    expect(sent.days[0].title).toBe('Chicken Chili');
    expect(sent.days[0].date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(sent.days[0].body).toContain('cumin');
  });

  it('mirrors the shopping list onto the Skylight grocery list', async () => {
    readNote.mockImplementation(
      (async (title: string) => {
        if (title === 'Meal plan') return PLAN_RESPONSE;
        if (title === 'Shopping list') {
          return { status: 'SUCCESS' as const, message: '#shopping-list\n\n- [ ] 2 cups flour\n- [x] salt', service: 'note_read' };
        }
        return RECIPE_RESPONSE;
      }) as never,
    );
    mirrorSkylightList.mockResolvedValue({ status: 'SUCCESS', message: 'Added 2 item(s)', added: 2, removed: 0 } as never);
    renderRecipes();
    await screen.findByText('Pancakes');
    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));
    await screen.findByTestId('meal-plan');

    fireEvent.click(screen.getByTestId('list-send-skylight'));

    await waitFor(() => expect(mirrorSkylightList).toHaveBeenCalled());
    expect(mirrorSkylightList).toHaveBeenCalledWith({ items: ['2 cups flour', 'salt'] });
  });

  it('does not push the week when Skylight is not connected', async () => {
    getSkylightMeals.mockResolvedValue({ status: 'SUCCESS', configured: false, categories: [] } as never);
    renderRecipes();
    await screen.findByText('Pancakes');
    fireEvent.click(await screen.findByRole('button', { name: /week plan/i }));
    await screen.findByTestId('meal-plan');

    fireEvent.click(screen.getByTestId('plan-send-skylight'));

    expect(pushSkylightWeek).not.toHaveBeenCalled();
  });
});
