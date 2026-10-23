import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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
      syncNotesRag: vi.fn(),
      importRecipe: vi.fn(),
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
const deleteNote = vi.mocked(api.deleteNote);
const syncNotesRag = vi.mocked(api.syncNotesRag);
const importRecipe = vi.mocked(api.importRecipe);
const getTalkConversations = vi.mocked(api.getTalkConversations);
const sendTalkMessage = vi.mocked(api.sendTalkMessage);

function renderRecipes() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <Recipes />
      </QueryClientProvider>
    </MemoryRouter>,
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
    syncNotesRag.mockResolvedValue({ status: 'SUCCESS', message: 'queued', service: 'notes_sync_rag' });
    importRecipe.mockResolvedValue({ status: 'FAILURE', message: 'not stubbed', service: 'recipe_import' });
    getTalkConversations.mockResolvedValue({
      status: 'SUCCESS',
      message: '',
      service: 'talk_conversations',
      detail: { conversations: [{ token: 'room-1', display_name: 'Family' }] },
    } as never);
    sendTalkMessage.mockResolvedValue({ status: 'SUCCESS', message: '', service: 'talk_send' });
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

  it('deletes a recipe after asking', async () => {
    const confirm = vi.fn(() => true);
    vi.stubGlobal('confirm', confirm);
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByLabelText('Delete Pancakes'));
    await waitFor(() => expect(deleteNote).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('edits an open recipe in place, without stacking the stored header', async () => {
    readNote.mockResolvedValue({ status: 'SUCCESS', message: '# Pancakes\nCategory: Recipes\n\nflour, milk', service: 'note_read' });
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByText('Pancakes'));
    await waitFor(() => expect((screen.getByLabelText('Recipe body') as HTMLTextAreaElement).value).toBe('flour, milk'));
    fireEvent.change(screen.getByLabelText('Recipe body'), { target: { value: 'flour, milk, eggs' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() =>
      expect(writeNote).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Pancakes', content: 'flour, milk, eggs', path: `${RECIPES_DIR}/Pancakes.md` }),
      ),
    );
    expect(deleteNote).not.toHaveBeenCalled();
  });

  it('renaming a recipe writes the new one, then removes the old file', async () => {
    writeNote.mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'note_write', detail: { path: `${RECIPES_DIR}/Crepes.md` } });
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByText('Pancakes'));
    await waitFor(() => expect(readNote).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText('Recipe name'), { target: { value: 'Crepes' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(deleteNote).toHaveBeenCalledWith('Pancakes', 'nextcloud', `${RECIPES_DIR}/Pancakes.md`, undefined));
    expect(writeNote).toHaveBeenCalledWith(expect.objectContaining({ title: 'Crepes', path: undefined }));
  });

  it('shares a recipe from the list to a family chat', async () => {
    readNote.mockResolvedValue({
      status: 'SUCCESS',
      message: '# Pancakes\nCategory: Recipes\n\nflour, milk, syrup',
      service: 'note_read',
    });
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByLabelText('Share Pancakes'));
    fireEvent.click(await screen.findByTestId('bible-share-chat-target'));

    await waitFor(() =>
      expect(sendTalkMessage).toHaveBeenCalledWith({
        token: 'room-1',
        message: expect.stringContaining('🍽 Pancakes'),
      }),
    );
    const sent = sendTalkMessage.mock.calls[0][0].message;
    expect(sent).toContain('flour, milk, syrup');
    expect(sent).not.toContain('Category:');
  });

  it('teaches Jarvis from the Recipes page', async () => {
    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('recipes')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: /teach jarvis/i }));

    await waitFor(() =>
      expect(syncNotesRag).toHaveBeenCalledWith({ directories: [RECIPES_DIR] }),
    );
  });

  it('imports a recipe from a pasted link', async () => {
    importRecipe.mockResolvedValue({
      status: 'SUCCESS',
      message: 'Found “Best Lemonade”.',
      service: 'recipe_import',
      detail: {
        recipe: {
          title: 'Best Lemonade',
          description: 'Cold and tart.',
          ingredients: ['4 cups water', '1 cup lemon juice', '1/2 cup sugar'],
          instructions: ['Juice the lemons.', 'Stir and chill.'],
          prep_minutes: 15,
          cook_minutes: null,
          total_minutes: 75,
          yield: '6 servings',
          image: null,
          source_url: 'https://example.com/recipes/lemonade',
          site: 'example.com',
        },
      },
    } as never);

    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('recipes')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: /import a link/i }));
    fireEvent.change(screen.getByLabelText('Recipe link'), {
      target: { value: 'https://example.com/recipes/lemonade' },
    });
    fireEvent.click(screen.getByRole('button', { name: /read the link/i }));

    expect(await screen.findByTestId('recipe-import-preview')).toBeTruthy();
    expect(screen.getByText('Best Lemonade')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /save to recipes/i }));

    await waitFor(() => expect(writeNote).toHaveBeenCalled());
    const body = writeNote.mock.calls[0][0] as { title: string; content: string; category: string };
    expect(body.title).toBe('Best Lemonade');
    expect(body.category).toBe(RECIPES_DIR);
    expect(body.content).toContain('Tags:');
    expect(body.content).toContain('- [ ] 4 cups water');
  });
});
