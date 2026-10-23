import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import Recipes, { RECIPES_DIR } from './Recipes';
import { api } from '../services/api';

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn(),
      listNotes: vi.fn(),
      readNote: vi.fn(),
      writeNote: vi.fn(),
      deleteNote: vi.fn(),
      syncNotesRag: vi.fn(),
      getTalkConversations: vi.fn(),
      sendTalkMessage: vi.fn(),
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
    listNotes.mockResolvedValue(listResponse(['Pancakes']) as never);
    readNote.mockImplementation(
      (async (title: string) => (title === 'Meal plan' ? PLAN_RESPONSE : RECIPE_RESPONSE)) as never,
    );
    writeNote.mockResolvedValue({ status: 'SUCCESS', message: '', service: 'note_write' } as never);
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
});
