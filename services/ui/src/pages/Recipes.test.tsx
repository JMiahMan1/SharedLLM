import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Recipes, { RECIPES_DIR } from './Recipes';
import { api } from '../services/api';

vi.mock('../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getMe: vi.fn(),
      listNotes: vi.fn(),
      readNote: vi.fn(),
      writeNote: vi.fn(),
      deleteNote: vi.fn(),
    },
  };
});

const getMe = vi.mocked(api.getMe);
const listNotes = vi.mocked(api.listNotes);
const readNote = vi.mocked(api.readNote);
const writeNote = vi.mocked(api.writeNote);
const deleteNote = vi.mocked(api.deleteNote);

function renderRecipes() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Recipes />
    </QueryClientProvider>,
  );
}

const listResponse = (titles: string[]) => ({
  status: 'SUCCESS' as const,
  message: '',
  service: 'note_list',
  detail: { notes: titles.map((title) => ({ title, path: `${RECIPES_DIR}/${title}.md` })) },
});

describe('Recipes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getMe.mockResolvedValue({ username: 'mom', is_admin: false } as never);
    listNotes.mockResolvedValue(listResponse(['Pancakes']));
    readNote.mockResolvedValue({ status: 'SUCCESS', message: 'flour, milk', service: 'note_read' });
    writeNote.mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'note_write' });
    deleteNote.mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'note_delete' });
  });

  it('lists recipes from the Recipes folder in Nextcloud', async () => {
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());
    expect(listNotes).toHaveBeenCalledWith(
      expect.objectContaining({ directories: [RECIPES_DIR], as_user: undefined }),
    );
  });

  it('invites the first recipe when the book is empty', async () => {
    listNotes.mockResolvedValue(listResponse([]));
    renderRecipes();
    await waitFor(() => expect(screen.getByText(/No recipes yet/)).toBeTruthy());
  });

  it('opens a recipe into the editor', async () => {
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByText('Pancakes'));
    await waitFor(() => expect(screen.getByLabelText('Recipe body').getAttribute('value')).toBe(null));
    expect((screen.getByLabelText('Recipe body') as HTMLTextAreaElement).value).toBe('flour, milk');
  });

  it('saves a recipe into the Recipes folder as the caller', async () => {
    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('recipes')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Recipe name'), { target: { value: 'Soup' } });
    fireEvent.change(screen.getByLabelText('Recipe body'), { target: { value: 'stock, bones' } });
    fireEvent.click(screen.getByRole('button', { name: /save recipe/i }));

    await waitFor(() =>
      expect(writeNote).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Soup', content: 'stock, bones', category: RECIPES_DIR, as_user: undefined }),
      ),
    );
  });

  it('refuses to save an unnamed recipe', async () => {
    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('recipes')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /save recipe/i }));
    expect(writeNote).not.toHaveBeenCalled();
  });

  it('hides the shared-cookbook switch from non-admins', async () => {
    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('recipes')).toBeTruthy());
    expect(screen.queryByTestId('send-as-selector')).toBeNull();
  });

  it('lets an admin keep the cookbook in the Admin account', async () => {
    getMe.mockResolvedValue({ username: 'dad', is_admin: true } as never);
    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('send-as-selector')).toBeTruthy());

    fireEvent.click(screen.getByRole('radio', { name: /admin/i }));
    await waitFor(() => expect(listNotes).toHaveBeenCalledWith(expect.objectContaining({ as_user: 'admin' })));
    expect(screen.getByTestId('send-as-badge')).toBeTruthy();
  });

  it('deletes a recipe', async () => {
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByLabelText('Delete Pancakes'));
    await waitFor(() => expect(deleteNote).toHaveBeenCalled());
  });
});
