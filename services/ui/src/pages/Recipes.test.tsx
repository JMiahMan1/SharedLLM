import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Recipes, { RECIPES_DIR } from './Recipes';
import { api } from '../services/api';
import { confirmDialog } from '../components/ui/confirmDialog';

// Destructive actions draw the app's own dialog now, not the browser's.
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
const confirmMock = vi.mocked(confirmDialog);

vi.mock('../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getMe: vi.fn(),
      getHealthProfile: vi.fn(),
      listNotes: vi.fn(),
      readNote: vi.fn(),
      writeNote: vi.fn(),
      deleteNote: vi.fn(),
      syncNotesRag: vi.fn(),
      importRecipe: vi.fn(),
      searchRecipes: vi.fn(),
      fetchRecipe: vi.fn(),
      recipeSources: vi.fn(),
      estimateRecipeNutrition: vi.fn(),
      readRecipePhoto: vi.fn(),
      noteAttachment: vi.fn(),
      getTalkConversations: vi.fn(),
      sendTalkMessage: vi.fn(),
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
const deleteNote = vi.mocked(api.deleteNote);
const syncNotesRag = vi.mocked(api.syncNotesRag);
const importRecipe = vi.mocked(api.importRecipe);
const searchRecipes = vi.mocked(api.searchRecipes);
const fetchRecipe = vi.mocked(api.fetchRecipe);
const recipeSources = vi.mocked(api.recipeSources);
const estimateRecipeNutrition = vi.mocked(api.estimateRecipeNutrition);
const readRecipePhoto = vi.mocked(api.readRecipePhoto);
const noteAttachment = vi.mocked(api.noteAttachment);
const getTalkConversations = vi.mocked(api.getTalkConversations);
const sendTalkMessage = vi.mocked(api.sendTalkMessage);
const getSkylightMeals = vi.mocked(api.getSkylightMeals);
const sendSkylightRecipe = vi.mocked(api.sendSkylightRecipe);
const pushSkylightWeek = vi.mocked(api.pushSkylightWeek);
const mirrorSkylightList = vi.mocked(api.mirrorSkylightList);

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
    vi.mocked(api.getHealthProfile).mockResolvedValue({ kid_mode: false } as never);
    listNotes.mockResolvedValue(listResponse(['Pancakes']));
    readNote.mockResolvedValue({ status: 'SUCCESS', message: 'flour, milk', service: 'note_read' });
    writeNote.mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'note_write' });
    deleteNote.mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'note_delete' });
    syncNotesRag.mockResolvedValue({ status: 'SUCCESS', message: 'queued', service: 'notes_sync_rag' });
    importRecipe.mockResolvedValue({ status: 'FAILURE', message: 'not stubbed', service: 'recipe_import' });
    searchRecipes.mockResolvedValue({ status: 'FAILURE', message: 'not stubbed', service: 'recipe_import' });
    recipeSources.mockResolvedValue([]);
    readRecipePhoto.mockResolvedValue({
      raw_text: '',
      recipe: {
        title: '',
        description: '',
        ingredients: [],
        instructions: [],
        prep_minutes: null,
        cook_minutes: null,
        total_minutes: null,
        yield: '',
        image: null,
        notes: '',
      },
      uncertain: [],
      missing: ['title', 'ingredients', 'steps'],
    });
    noteAttachment.mockResolvedValue({ status: 'FAILURE', message: 'not stubbed', service: 'note_attachment' });
    getTalkConversations.mockResolvedValue({
      status: 'SUCCESS',
      message: '',
      service: 'talk_conversations',
      detail: { conversations: [{ token: 'room-1', display_name: 'Family' }] },
    } as never);
    sendTalkMessage.mockResolvedValue({ status: 'SUCCESS', message: '', service: 'talk_send' });
    getSkylightMeals.mockResolvedValue({ status: 'SUCCESS', configured: true, categories: [] });
    sendSkylightRecipe.mockResolvedValue({ status: 'SUCCESS', message: 'Sent “Pancakes” to the Skylight Recipe Box' });
    pushSkylightWeek.mockResolvedValue({ status: 'SUCCESS', planned: [], failed: [] });
    mirrorSkylightList.mockResolvedValue({ status: 'SUCCESS', added: 0, removed: 0 });
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
    confirmMock.mockResolvedValue(true);
    const confirm = confirmMock;
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

    await waitFor(() => expect(deleteNote).toHaveBeenCalledWith(
        'Pancakes', 'nextcloud', `${RECIPES_DIR}/Pancakes.md`, undefined, `${RECIPES_DIR}/Crepes.md`,
      ),
    );
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

  it('marks a search result already imported instead of offering it again', async () => {
    searchRecipes.mockResolvedValue({
      status: 'SUCCESS',
      message: 'Found 1 recipe(s).',
      service: 'recipe_import',
      detail: {
        results: [
          {
            id: '52772',
            source: 'themealdb',
            title: 'Chocolate Gateau',
            description: 'Dessert · French',
            image: null,
            source_url: 'https://www.themealdb.com/meal/52772',
            site: 'themealdb.com',
            ingredients: ['175g Butter'],
            instructions: ['Bake.'],
          },
        ],
      },
    } as never);
    recipeSources.mockResolvedValue(['https://www.themealdb.com/meal/52772']);

    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('recipes')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /import a link/i }));
    fireEvent.change(screen.getByLabelText('Search recipes'), { target: { value: 'gateau' } });
    fireEvent.click(screen.getByRole('button', { name: /^search$/i }));

    expect(await screen.findByText('Already in Recipes')).toBeTruthy();
    const card = screen.getByText('Chocolate Gateau').closest('button');
    expect(card).toBeDisabled();
    expect(fetchRecipe).not.toHaveBeenCalled();
  });

  it('filters the shelf by name', async () => {
    listNotes.mockResolvedValue(listResponse(['Pancakes', 'Chicken Chili']));
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Filter recipes'), { target: { value: 'chick' } });
    expect(screen.queryByText('Pancakes')).toBeNull();
    expect(screen.getByText('Chicken Chili')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Filter recipes'), { target: { value: 'zzz' } });
    expect(screen.getByText('No recipes match “zzz”.')).toBeTruthy();
  });

  it('searches TheMealDB and saves a pick', async () => {
    searchRecipes.mockResolvedValue({
      status: 'SUCCESS',
      message: 'Found 1 recipe(s).',
      service: 'recipe_import',
      detail: {
        recipes: [
          {
            title: 'Chocolate Gateau',
            description: 'Dessert · French',
            ingredients: ['175g Butter', '200g Dark Chocolate'],
            instructions: ['Melt the butter.', 'Bake.'],
            prep_minutes: null,
            cook_minutes: null,
            total_minutes: null,
            yield: '',
            image: null,
            source_url: 'https://www.themealdb.com/meal/52772',
            site: 'themealdb.com',
          },
        ],
      },
    } as never);

    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('recipes')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: /import a link/i }));
    fireEvent.change(screen.getByLabelText('Search recipes'), { target: { value: 'gateau' } });
    fireEvent.click(screen.getByRole('button', { name: /^search$/i }));

    const card = await screen.findByText('Chocolate Gateau');
    fireEvent.click(card);

    expect(await screen.findByTestId('recipe-import-preview')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /save to recipes/i }));

    await waitFor(() => expect(writeNote).toHaveBeenCalled());
    const body = writeNote.mock.calls[0][0] as { title: string; content: string };
    expect(body.title).toBe('Chocolate Gateau');
    expect(body.content).toContain('Source: https://www.themealdb.com/meal/52772');
    expect(body.content).toContain('- [ ] 175g Butter');
  });

  it('searches the shelf and reads a Food.com pick from the index', async () => {
    searchRecipes.mockResolvedValue({
      status: 'SUCCESS',
      message: 'Found 2 recipe(s).',
      service: 'recipe_import',
      detail: {
        results: [
          {
            id: 38,
            source: 'foodcom',
            title: 'Low-Fat Berry Blue Frozen Dessert',
            description: 'Frozen Desserts',
            image: null,
            total_minutes: 255,
            servings: 4,
            source_url: 'https://www.food.com/recipe/low-fat-berry-blue-frozen-dessert-38',
            site: 'food.com',
          },
          {
            id: '52772',
            source: 'themealdb',
            title: 'Chocolate Gateau',
            description: 'Dessert · French',
            image: null,
            source_url: 'https://www.themealdb.com/meal/52772',
            site: 'themealdb.com',
            ingredients: ['175g Butter'],
            instructions: ['Bake.'],
          },
        ],
      },
      sources: ['foodcom', 'themealdb'],
      offline_sources: [],
      seed: { present: true, results: 1 },
    } as never);
    fetchRecipe.mockResolvedValue({
      status: 'SUCCESS',
      message: '',
      service: 'recipe_import',
      detail: {
        recipe: {
          title: 'Low-Fat Berry Blue Frozen Dessert',
          description: 'Frozen Desserts',
          ingredients: ['4 cups blueberries, fresh or frozen', '0.25 cup vanilla yogurt'],
          instructions: ['Mix everything.', 'Freeze until firm.'],
          prep_minutes: 10,
          cook_minutes: 180,
          total_minutes: 190,
          yield: '4 servings',
          image: null,
          source_url: 'https://www.food.com/recipe/low-fat-berry-blue-frozen-dessert-38',
          site: 'food.com',
        },
      },
    } as never);

    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('recipes')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: /import a link/i }));
    fireEvent.change(screen.getByLabelText('Search recipes'), { target: { value: 'berry' } });
    fireEvent.click(screen.getByRole('button', { name: /^search$/i }));

    const card = await screen.findByText('Low-Fat Berry Blue Frozen Dessert');
    fireEvent.click(card);

    await waitFor(() => expect(fetchRecipe).toHaveBeenCalledWith(38));
    expect(await screen.findByTestId('recipe-import-preview')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /save to recipes/i }));

    await waitFor(() => expect(writeNote).toHaveBeenCalled());
    const body = writeNote.mock.calls[0][0] as { title: string; content: string };
    expect(body.title).toBe('Low-Fat Berry Blue Frozen Dessert');
    expect(body.content).toContain(
      'Source: https://www.food.com/recipe/low-fat-berry-blue-frozen-dessert-38',
    );
    expect(body.content).toContain('4 cups blueberries, fresh or frozen');
  });

  it("shows an open recipe's per-serving nutrition and household estimate", async () => {
    readNote.mockResolvedValue({
      status: 'SUCCESS',
      message: [
        '# Chili',
        'Category: Recipes',
        '',
        'Times: prep 15 min · servings 7 (scaled from 4)',
        '',
        '## Nutrition',
        '*Per serving, as stated by the submitter - foodcom:* 350.2 kcal · 12 g fat · 30 g carbs · 25 g protein',
      ].join('\n'),
      service: 'note_read',
    });
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByText('Pancakes'));
    const line = await screen.findByTestId('recipe-nutrition');
    expect(line).toHaveTextContent('Per serving (foodcom): 350 kcal');
    expect(line).toHaveTextContent('whole recipe (7 servings): 2451 kcal');
  });

  it('estimates a recipe with no stated nutrition from the USDA seed', async () => {
    readNote.mockResolvedValue({
      status: 'SUCCESS',
      message: '## Ingredients\n- [ ] 2 lb chicken\n- [ ] flour',
      service: 'note_read',
    });
    estimateRecipeNutrition.mockResolvedValue({
      estimate: true,
      source: 'usda_fdc',
      seed: { present: true },
      ingredients: [
        {
          text: '2 lb chicken',
          status: 'matched',
          food: 'Chicken, broilers or fryers, meat and skin, raw',
          code: 'foundation:1',
          grams: 907,
          nutrients: { calories: 1650, protein_g: 250, carbs_g: 0, fat_g: 60, fiber_g: null, sugar_g: null, sodium_mg: null },
        },
        { text: 'flour', status: 'unknown', reason: 'No USDA match for “flour”' },
      ],
      matched: 1,
      unknown: 1,
      totals: { calories: 1650, protein_g: 250, carbs_g: 0, fat_g: 60, fiber_g: null, sugar_g: null, sodium_mg: null },
      complete: true,
    } as never);
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByText('Pancakes'));
    fireEvent.click(await screen.findByTestId('recipe-estimate-button'));

    const result = await screen.findByTestId('recipe-estimate-result');
    expect(result).toHaveTextContent('Estimate from USDA food data');
    expect(result).toHaveTextContent('unknown');
    expect(screen.getByTestId('recipe-estimate-total')).toHaveTextContent('1650 kcal');
    expect(estimateRecipeNutrition).toHaveBeenCalledWith(['2 lb chicken', 'flour']);
  });

  it('hides the USDA estimate in kid mode', async () => {
    vi.mocked(api.getHealthProfile).mockResolvedValue({ kid_mode: true } as never);
    readNote.mockResolvedValue({
      status: 'SUCCESS',
      message: '## Ingredients\n- [ ] 2 lb chicken',
      service: 'note_read',
    });
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByText('Pancakes'));
    await waitFor(() =>
      expect((screen.getByLabelText('Recipe body') as HTMLTextAreaElement).value).toContain('chicken'),
    );
    expect(screen.queryByTestId('recipe-estimate-button')).toBeNull();
  });

  it('reads a recipe photo into a draft, flags uncertain words, and keeps the photo', async () => {    writeNote.mockResolvedValue({
      status: 'SUCCESS',
      message: 'ok',
      service: 'note_write',
      detail: { path: "Recipes/Nana's Pancakes.md", etag: 'v1' },
    } as never);
    readRecipePhoto.mockResolvedValue({
      raw_text: "Nana's Pancakes\n2 cups flour\n1 tsp salt\n\nMix. Bake.",
      recipe: {
        title: "Nana's Pancakes",
        description: '',
        ingredients: ['2 cups flour', '1 tsp salt'],
        instructions: ['Mix.', 'Bake.'],
        prep_minutes: 10,
        cook_minutes: 20,
        total_minutes: 30,
        yield: '4',
        image: null,
        notes: 'Best with butter.',
      },
      uncertain: ['salt (line 3)'],
      missing: [],
    });
    noteAttachment.mockResolvedValue({
      status: 'SUCCESS',
      message: '',
      service: 'note_attachment',
      detail: { link: '.attachments.9/page.jpg', name: 'page.jpg', mime: 'image/jpeg' },
    } as never);

    renderRecipes();
    await waitFor(() => expect(screen.getByTestId('recipes')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /import a link/i }));

    const file = new File(['bytes'], 'page.jpg', { type: 'image/jpeg' });
    fireEvent.change(screen.getByLabelText('Recipe photo'), { target: { files: [file] } });

    expect(await screen.findByTestId('recipe-import-preview')).toBeTruthy();
    expect(readRecipePhoto).toHaveBeenCalledWith(file);
    expect(screen.getByTestId('recipe-photo-review')).toBeTruthy();
    expect(screen.getByText(/check against the page/i)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /save to recipes/i }));

    await waitFor(() => expect(writeNote).toHaveBeenCalled());
    const first = writeNote.mock.calls[0][0] as { title: string; content: string };
    expect(first.title).toBe("Nana's Pancakes");
    expect(first.content).toContain('#photo-import');
    expect(first.content).toContain('- [ ] 2 cups flour');

    await waitFor(() => expect(noteAttachment).toHaveBeenCalled());
    const upload = noteAttachment.mock.calls[0][0] as { action: string; path: string };
    expect(upload.action).toBe('upload');
    expect(upload.path).toBe("Recipes/Nana's Pancakes.md");
    // The link lands in the note on the second save.
    await waitFor(() => expect(writeNote).toHaveBeenCalledTimes(2));
    expect((writeNote.mock.calls[1][0] as { content: string }).content).toContain('.attachments.9/page.jpg');
  });

  it('sends an open recipe to Skylight from the editor', async () => {
    readNote.mockResolvedValue({
      status: 'SUCCESS',
      message: '# Pancakes\nCategory: Recipes\n\n## Ingredients\n- [ ] flour\n\n## Steps\n1. Mix.',
      service: 'note_read',
    });
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByText('Pancakes'));
    await waitFor(() => expect(screen.getByTestId('recipe-send-skylight')).toBeTruthy());
    fireEvent.click(screen.getByTestId('recipe-send-skylight'));

    await waitFor(() =>
      expect(sendSkylightRecipe).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Pancakes', body: expect.stringContaining('flour') }),
      ),
    );
  });

  it('sends a recipe from its card, reading the note first', async () => {
    renderRecipes();
    await waitFor(() => expect(screen.getByText('Pancakes')).toBeTruthy());

    fireEvent.click(screen.getByLabelText('Send Pancakes to Skylight'));

    await waitFor(() => expect(sendSkylightRecipe).toHaveBeenCalled());
    expect(sendSkylightRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Pancakes', body: 'flour, milk' }),
    );
  });

  it('shows the no-credentials state and never fakes a send', async () => {
    getSkylightMeals.mockResolvedValue({ status: 'SUCCESS', configured: false, categories: [] });
    renderRecipes();

    await waitFor(() => expect(screen.getByTestId('skylight-off')).toBeTruthy());
    fireEvent.click(screen.getByLabelText('Send Pancakes to Skylight'));

    // Nothing was sent and the recipe note was not even read for the push.
    expect(sendSkylightRecipe).not.toHaveBeenCalled();
  });
});
