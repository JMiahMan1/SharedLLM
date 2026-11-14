import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import * as lists from '../lib/todoLists';
import type { TodoList } from '../lib/todoLists';
import Lists from './Lists';

vi.mock('../lib/todoLists', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return {
    ...real, getLists: vi.fn(), addItem: vi.fn(), updateItem: vi.fn(), removeItem: vi.fn(), moveItem: vi.fn(),
    clearCompleted: vi.fn(),
  };
});
vi.mock('../services/api', () => ({ api: {}, apiClient: {} }));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const features = (extra: Partial<TodoList['features']> = {}): TodoList['features'] => ({
  create: true, delete: true, update: true, move: true, due_date: false, due_datetime: false, description: false, ...extra,
});

const SHOPPING: TodoList = {
  entity_id: 'todo.shopping_list', name: 'Shopping List', open: 2, available: true, features: features(), items: [
    { uid: 'u1', summary: 'Milk', status: 'needs_action', due: null, description: null },
    { uid: 'u2', summary: 'Eggs', status: 'needs_action', due: null, description: null },
    { uid: 'u3', summary: 'Rye bread', status: 'completed', due: null, description: null },
  ],
};
const REMINDERS: TodoList = {
  entity_id: 'todo.reminders', name: 'Reminders', open: 1, available: true,
  features: features({ due_date: true, due_datetime: true, description: true }), items: [
    { uid: 'r1', summary: 'Call the vet', status: 'needs_action', due: '2026-11-20', description: null },
  ],
};

function wrap(path = '/lists') {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><Lists /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the Lists page', () => {
  beforeEach(() => {
    vi.mocked(lists.getLists).mockResolvedValue([REMINDERS, SHOPPING]);
  });

  it('shows the first list, a tab per list and the recipe list as a separate link', async () => {
    wrap();
    expect(await screen.findByRole('heading', { name: 'Reminders' })).toBeTruthy();
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Reminders1', 'Shopping List2']);
    expect(screen.getByText(/Due /)).toBeTruthy();
    expect(screen.getByTestId('recipe-list-link').getAttribute('href')).toBe('/recipes?tab=list');
    fireEvent.click(screen.getByRole('tab', { name: /Shopping List/ }));
    expect(await screen.findByRole('heading', { name: 'Shopping List' })).toBeTruthy();
  });

  it('adds an item and shows the list the server sends back', async () => {
    vi.mocked(lists.addItem).mockResolvedValue({ ...SHOPPING, items: [...SHOPPING.items,
      { uid: 'u4', summary: 'Jam', status: 'needs_action', due: null, description: null }] });
    wrap('/lists?list=todo.shopping_list');
    const input = await screen.findByRole('textbox', { name: 'Add to Shopping List' });
    // The shopping list has no due dates, so there is no date field.
    expect(screen.queryByLabelText('Due date (optional)')).toBeNull();
    fireEvent.change(input, { target: { value: '  Jam ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(lists.addItem).toHaveBeenCalledWith('todo.shopping_list', 'Jam', {}));
    expect(await screen.findByText('Jam')).toBeTruthy();
  });

  it('ticks off, reorders, deletes and clears done items by id', async () => {
    vi.mocked(lists.updateItem).mockResolvedValue(SHOPPING);
    vi.mocked(lists.moveItem).mockResolvedValue(SHOPPING);
    vi.mocked(lists.removeItem).mockResolvedValue(SHOPPING);
    vi.mocked(lists.clearCompleted).mockResolvedValue(SHOPPING);
    wrap('/lists?list=todo.shopping_list');
    fireEvent.click(await screen.findByRole('button', { name: 'Mark Milk done' }));
    await waitFor(() => expect(lists.updateItem).toHaveBeenCalledWith('todo.shopping_list', 'u1', { status: 'completed' }));
    // The first item cannot go up, the last cannot go down.
    expect(screen.queryByRole('button', { name: 'Move Milk up' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Move Eggs down' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Move Eggs up' }));
    await waitFor(() => expect(lists.moveItem).toHaveBeenCalledWith('todo.shopping_list', 'u2', null));
    fireEvent.click(screen.getByRole('button', { name: 'Delete Milk' }));
    await waitFor(() => expect(lists.removeItem).toHaveBeenCalledWith('todo.shopping_list', 'u1'));
    const done = screen.getByTestId('completed-items');
    fireEvent.click(within(done).getByText(/Done \(1\)/));
    fireEvent.click(within(done).getByRole('button', { name: 'Mark Rye bread not done' }));
    await waitFor(() => expect(lists.updateItem).toHaveBeenCalledWith('todo.shopping_list', 'u3', { status: 'needs_action' }));
    fireEvent.click(within(done).getByRole('button', { name: 'Clear done items' }));
    await waitFor(() => expect(lists.clearCompleted).toHaveBeenCalledWith('todo.shopping_list'));
  });

  it('renames an item in place', async () => {
    vi.mocked(lists.updateItem).mockResolvedValue(SHOPPING);
    wrap('/lists?list=todo.shopping_list');
    fireEvent.click(await screen.findByRole('button', { name: 'Rename Milk' }));
    const field = screen.getByRole('textbox', { name: 'Rename Milk' });
    fireEvent.change(field, { target: { value: 'Oat milk' } });
    fireEvent.submit(field.closest('form') as HTMLFormElement);
    await waitFor(() => expect(lists.updateItem).toHaveBeenCalledWith('todo.shopping_list', 'u1', { summary: 'Oat milk' }));
  });

  it('edits an item\'s notes where the list keeps them, and its name with them', async () => {
    vi.mocked(lists.updateItem).mockResolvedValue(REMINDERS);
    wrap('/lists?list=todo.reminders');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Call the vet' }));
    const notes = screen.getByRole('textbox', { name: 'Notes for Call the vet' });
    fireEvent.change(notes, { target: { value: '  Ask about the booster  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lists.updateItem).toHaveBeenCalledWith('todo.reminders', 'r1',
      { description: 'Ask about the booster' }));
    vi.mocked(lists.updateItem).mockClear();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Call the vet' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Rename Call the vet' }), { target: { value: 'Call the vet at 9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lists.updateItem).toHaveBeenCalledWith('todo.reminders', 'r1', { summary: 'Call the vet at 9' }));
  });

  it('clears notes, and saves nothing when nothing changed', async () => {
    const withNotes: TodoList = { ...REMINDERS, items: [{ ...REMINDERS.items[0], description: 'Old note' }] };
    vi.mocked(lists.getLists).mockResolvedValue([withNotes]);
    vi.mocked(lists.updateItem).mockClear().mockResolvedValue(REMINDERS);
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Call the vet' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(lists.updateItem).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Call the vet' }));
    expect(screen.queryByRole('button', { name: 'Delete Call the vet' })).toBeNull();
    fireEvent.change(screen.getByRole('textbox', { name: 'Notes for Call the vet' }), { target: { value: ' ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lists.updateItem).toHaveBeenCalledWith('todo.reminders', 'r1', { description: null }));
  });

  it('a list without notes keeps the plain rename', async () => {
    wrap('/lists?list=todo.shopping_list');
    fireEvent.click(await screen.findByRole('button', { name: 'Rename Milk' }));
    expect(screen.queryByRole('textbox', { name: /Notes for/ })).toBeNull();
  });

  it('a list that cannot be reordered offers no move buttons', async () => {
    vi.mocked(lists.getLists).mockResolvedValue([{ ...SHOPPING, features: features({ move: false }) }]);
    wrap();
    await screen.findByRole('heading', { name: 'Shopping List' });
    expect(screen.queryByRole('button', { name: /Move .* (up|down)/ })).toBeNull();
    expect(screen.queryByRole('tablist')).toBeNull();
  });

  it('says so when the home has no lists', async () => {
    vi.mocked(lists.getLists).mockResolvedValue([]);
    wrap();
    expect(await screen.findByText('No lists yet')).toBeTruthy();
  });
});
