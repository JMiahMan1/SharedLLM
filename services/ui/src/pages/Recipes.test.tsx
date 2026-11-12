import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Recipes from './Recipes';
import { api } from '../services/api';
import type { CookbookRecipe } from '../types/api';
import { confirmDialog } from '../components/ui/confirmDialog';

vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));

vi.mock('../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getMe: vi.fn(),
      getHealthProfile: vi.fn(),
      recipeCatalog: vi.fn(),
      cookbookRead: vi.fn(),
      cookbookSave: vi.fn(),
      cookbookDelete: vi.fn(),
      cookbookAttach: vi.fn(),
      cookbookPhoto: vi.fn(),
      cookbookSuggest: vi.fn(),
      cookbookSuggestions: vi.fn(),
      cookbookResolve: vi.fn(),
      cookbookPersonal: vi.fn(),
      cookbookAdopt: vi.fn(),
      rateRecipe: vi.fn(),
      recipeMade: vi.fn(),
      recipeUnmade: vi.fn(),
      readNote: vi.fn(),
      writeNote: vi.fn(),
      deleteNote: vi.fn(),
      syncNotesRag: vi.fn(),
      importRecipe: vi.fn(),
      searchRecipes: vi.fn(),
      fetchRecipe: vi.fn(),
      recipeSources: vi.fn(),
      mealPlanIcs: vi.fn(),
      estimateRecipeNutrition: vi.fn(),
      readRecipePhoto: vi.fn(),
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

const m = vi.mocked(api);

function recipe(title: string, extra: Partial<CookbookRecipe> = {}): CookbookRecipe {
  return {
    title,
    path: `Recipes/${title.replace(/ /g, '-')}.md`,
    tags: [],
    meal: 'main',
    proteins: ['vegetarian'],
    total_minutes: 30,
    servings: 4,
    calories: null,
    ingredients: 3,
    level: null,
    owner: 'mom',
    owner_label: 'Mom',
    can_change: true,
    source: '',
    image: null,
    staples: [],
    rating: null,
    ratings: 0,
    my_rating: null,
    made: 0,
    made_by_me: 0,
    last_made: null,
    ...extra,
  };
}

const CHILI = recipe('Chicken Chili', { meal: 'soup', proteins: ['chicken'], rating: 4.5, ratings: 2, made: 7, total_minutes: 40 });
const COOKIES = recipe('Sugar Cookies', { meal: 'dessert', rating: 5, ratings: 1, made: 12, total_minutes: 25 });
const ROAST = recipe('Pot Roast', { proteins: ['beef'], made: 1, total_minutes: 240, can_change: false, owner: 'house', owner_label: 'an admin' });

const CHILI_BODY = `Tags: #dinner
Times: prep 10 min · cook 30 min · servings 4

## Ingredients
- [ ] 2 lb chicken
- [ ] 1 1/2 cups beans
- [ ] salt to taste

## Steps
1. Brown the chicken.
2. Simmer.`;

const PLAN = '# Meal plan\nCategory: Recipes\n\n#meal-plan\n\n- [ ] Mon · Chicken Chili\n- [ ] Tue ·\n';

function renderAt(entry = '/recipes') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <QueryClientProvider client={queryClient}>
        <Recipes />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const ok = (message = '', detail: Record<string, unknown> = {}) => ({ status: 'SUCCESS' as const, message, service: 'cookbook', detail });
const stat = (over: Record<string, unknown> = {}) => ({ recipe: CHILI.path, rating: 4.5, ratings: 2, mine: null, made: 7, made_by_me: 0, last_made: null, ...over });

beforeEach(() => {
  vi.clearAllMocks();
  m.getMe.mockResolvedValue({ username: 'mom', is_admin: false } as never);
  m.getHealthProfile.mockResolvedValue({ kid_mode: false } as never);
  m.recipeCatalog.mockResolvedValue({ recipes: [CHILI, COOKIES, ROAST], me: 'mom', is_admin: false });
  m.cookbookRead.mockResolvedValue(ok(CHILI_BODY, { path: CHILI.path, etag: '"e1"', can_change: true, owner_label: 'Mom' }));
  m.cookbookSave.mockResolvedValue(ok('saved', { path: CHILI.path }));
  m.cookbookSuggestions.mockResolvedValue([]);
  m.cookbookPersonal.mockResolvedValue([]);
  m.recipeSources.mockResolvedValue([]);
  m.readNote.mockImplementation(async (title: string) =>
    title === 'Meal plan' ? ({ status: 'SUCCESS', message: PLAN, service: 'note_read' }) : ({ status: 'FAILURE', message: 'not found', service: 'note_read' }),
  );
  m.writeNote.mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'note_write', detail: {} });
  m.deleteNote.mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'note_delete' });
  m.getSkylightMeals.mockResolvedValue({ status: 'SUCCESS', configured: true, categories: [] });
  m.sendSkylightRecipe.mockResolvedValue({ status: 'SUCCESS', message: 'Sent' });
  m.pushSkylightWeek.mockResolvedValue({ status: 'SUCCESS', planned: [], failed: [] });
  m.mirrorSkylightList.mockResolvedValue({ status: 'SUCCESS', added: 0, removed: 0 });
  m.syncNotesRag.mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'note_sync_rag' });
});

describe('browsing the cookbook', () => {
  it('shows every recipe as a card with its rating and times made', async () => {
    renderAt();
    const tile = await screen.findByTestId(`recipe-tile-${CHILI.path}`);
    expect(tile).toHaveTextContent('Chicken Chili');
    expect(tile).toHaveTextContent('Made 7×');
    expect(tile).toHaveTextContent('4.5');
    expect(screen.getByText('3 recipes')).toBeTruthy();
  });

  it('narrows by protein and kind of meal, and searches', async () => {
    renderAt();
    await screen.findByTestId(`recipe-tile-${CHILI.path}`);
    fireEvent.click(screen.getByTestId('protein-chip-beef'));
    expect(screen.queryByTestId(`recipe-tile-${CHILI.path}`)).toBeNull();
    expect(screen.getByTestId(`recipe-tile-${ROAST.path}`)).toBeTruthy();
    fireEvent.click(screen.getByTestId('protein-chip-beef'));
    fireEvent.click(screen.getByTestId('meal-chip-dessert'));
    expect(screen.getByText('1 of 3 recipes')).toBeTruthy();
    fireEvent.click(screen.getByTestId('meal-chip-dessert'));
    fireEvent.change(screen.getByLabelText('Search the cookbook'), { target: { value: 'chili' } });
    expect(screen.getByText('1 of 3 recipes')).toBeTruthy();
  });

  it('sorts by most made and quickest', async () => {
    renderAt();
    await screen.findByTestId(`recipe-tile-${CHILI.path}`);
    const order = () => screen.getAllByTestId(/^recipe-tile-/).map((el) => el.getAttribute('data-testid'));
    fireEvent.change(screen.getByLabelText('Sort recipes'), { target: { value: 'made' } });
    expect(order()[0]).toBe(`recipe-tile-${COOKIES.path}`);
    fireEvent.change(screen.getByLabelText('Sort recipes'), { target: { value: 'quick' } });
    expect(order()[2]).toBe(`recipe-tile-${ROAST.path}`);
  });
});

describe('the recipe card', () => {
  it('opens to read, with ingredients scaled to the servings asked for', async () => {
    renderAt();
    fireEvent.click(await screen.findByTestId(`recipe-tile-${CHILI.path}`));
    const list = await screen.findByTestId('recipe-ingredients');
    expect(list).toHaveTextContent('2 lb chicken');
    expect(screen.getByTestId('recipe-steps')).toHaveTextContent('Brown the chicken.');
    for (let i = 0; i < 4; i += 1) fireEvent.click(screen.getByLabelText('More servings'));
    expect(screen.getByTestId('recipe-servings')).toHaveTextContent('8 servings');
    expect(list).toHaveTextContent('4 lb chicken');
    expect(list).toHaveTextContent('3 cups beans');
    expect(list).toHaveTextContent('salt to taste');
  });

  it('shows the photographed original among the photos at the bottom of the card', async () => {
    m.cookbookRead.mockResolvedValue(ok(`${CHILI_BODY.replace('Tags: #dinner', 'Tags: #dinner #photo-import')}\n![card.jpg](.attachments.42/card.jpg)\n`, { path: CHILI.path, can_change: true, owner_label: 'Mom' }));
    renderAt();
    fireEvent.click(await screen.findByTestId(`recipe-tile-${CHILI.path}`));
    const photos = await screen.findByTestId('recipe-photos');
    expect(photos).toHaveTextContent('Original recipe');
    expect(screen.getByTestId('recipe-steps')).not.toHaveTextContent('.attachments');
  });

  it('adds a photo anyone took, and lets the owner choose the cover and remove one', async () => {
    m.cookbookRead.mockResolvedValue(ok(`${CHILI_BODY}\n![Photo by dad](.attachments.42/a.jpg)\n![Photo by mom](.attachments.42/b.jpg)\n`, { path: CHILI.path, can_change: true, owner_label: 'Mom', image: '.attachments.42/a.jpg' }));
    m.cookbookPhoto.mockResolvedValue({ status: 'SUCCESS', message: 'ok', service: 'cookbook', detail: {} } as never);
    renderAt();
    fireEvent.click(await screen.findByTestId(`recipe-tile-${CHILI.path}`));
    await screen.findByTestId('recipe-photos');
    expect(screen.getAllByTestId('recipe-photo')[0]).toHaveTextContent('Cover');

    fireEvent.change(screen.getByTestId('recipe-photo-input'), { target: { files: [new File(['x'], 'dinner.jpg', { type: 'image/jpeg' })] } });
    await waitFor(() => expect(m.cookbookPhoto).toHaveBeenCalledWith(expect.objectContaining({ path: CHILI.path, action: 'add', data: expect.any(String) })));

    fireEvent.click(screen.getByLabelText('Make Photo by mom the cover'));
    await waitFor(() => expect(m.cookbookPhoto).toHaveBeenCalledWith({ path: CHILI.path, action: 'cover', link: '.attachments.42/b.jpg' }));

    fireEvent.click(screen.getByLabelText('Remove Photo by dad'));
    await waitFor(() => expect(m.cookbookPhoto).toHaveBeenCalledWith({ path: CHILI.path, action: 'remove', link: '.attachments.42/a.jpg' }));
  });

  it("does not offer the cover or removal on someone else's recipe", async () => {
    m.cookbookRead.mockResolvedValue(ok(`${CHILI_BODY}\n![Photo by dad](.attachments.42/a.jpg)\n`, { path: CHILI.path, can_change: false, owner_label: 'Mom' }));
    renderAt();
    fireEvent.click(await screen.findByTestId(`recipe-tile-${ROAST.path}`));
    await screen.findByTestId('recipe-photos');
    expect(screen.getByTestId('recipe-photo-take')).toBeTruthy();
    expect(screen.queryByLabelText(/^Remove /)).toBeNull();
  });

  it('records stars and a making', async () => {
    m.rateRecipe.mockResolvedValue(stat({ rating: 4.7, ratings: 3, mine: 5 }));
    m.recipeMade.mockResolvedValue(stat({ made: 8, made_by_me: 1, last_made: '2026-11-01' }));
    renderAt(`/recipes?recipe=${encodeURIComponent(CHILI.path)}`);
    await screen.findByTestId('recipe-ingredients');
    fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Your rating' })).getByLabelText('5 stars'));
    await waitFor(() => expect(m.rateRecipe).toHaveBeenCalledWith(CHILI.path, 5));
    expect(await screen.findByTestId('recipe-family-rating')).toHaveTextContent('4.7 (3)');
    fireEvent.click(screen.getByTestId('recipe-made'));
    await waitFor(() => expect(m.recipeMade).toHaveBeenCalledWith(CHILI.path, expect.any(String)));
    await waitFor(() => expect(screen.getByTestId('recipe-made-count')).toHaveTextContent('8'));
  });

  it('adds the recipe to a day of the week plan', async () => {
    renderAt(`/recipes?recipe=${encodeURIComponent(COOKIES.path)}`);
    await screen.findByTestId('recipe-view');
    fireEvent.click(screen.getByRole('button', { name: /add to the week/i }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /^Tue/ }));
    await waitFor(() =>
      expect(m.writeNote).toHaveBeenCalledWith(expect.objectContaining({ title: 'Meal plan', content: expect.stringContaining('Tue · Sugar Cookies') })),
    );
  });

  it('edits with fields and saves the same markdown', async () => {
    renderAt(`/recipes?recipe=${encodeURIComponent(CHILI.path)}`);
    await screen.findByTestId('recipe-ingredients');
    fireEvent.click(screen.getByTestId('recipe-edit'));
    const steps = await screen.findByLabelText('Steps');
    fireEvent.change(steps, { target: { value: 'Brown the chicken.\nSimmer.\nServe with cornbread.' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(m.cookbookSave).toHaveBeenCalled());
    const sent = m.cookbookSave.mock.calls[0][0];
    expect(sent).toMatchObject({ title: 'Chicken Chili', path: CHILI.path, if_match: '"e1"' });
    expect(sent.content).toContain('- [ ] 1 1/2 cups beans');
    expect(sent.content).toContain('3. Serve with cornbread.');
    expect(sent.content).toContain('Times: prep 10 min · cook 30 min · servings 4');
  });

  it("sends an edit to someone else's recipe as a suggestion", async () => {
    m.cookbookRead.mockResolvedValue(ok(CHILI_BODY, { path: ROAST.path, can_change: false, owner_label: 'an admin' }));
    m.cookbookSuggest.mockResolvedValue(ok('Sent to an admin to approve.'));
    renderAt(`/recipes?recipe=${encodeURIComponent(ROAST.path)}`);
    await screen.findByTestId('recipe-ingredients');
    fireEvent.click(screen.getByLabelText('More actions'));
    expect(screen.queryByLabelText('Delete Pot Roast')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /suggest a change/i }));
    fireEvent.change(await screen.findByLabelText('Ingredients'), { target: { value: '3 lb chuck roast' } });
    fireEvent.change(screen.getByLabelText("Note for the recipe's owner"), { target: { value: 'bigger roast' } });
    fireEvent.click(screen.getByRole('button', { name: /suggest this change/i }));
    await waitFor(() =>
      expect(m.cookbookSuggest).toHaveBeenCalledWith({ path: ROAST.path, content: expect.stringContaining('- [ ] 3 lb chuck roast'), message: 'bigger roast' }),
    );
    expect(m.cookbookSave).not.toHaveBeenCalled();
  });

  it('deletes a recipe its owner opened, after asking', async () => {
    m.cookbookDelete.mockResolvedValue(ok('deleted'));
    renderAt(`/recipes?recipe=${encodeURIComponent(CHILI.path)}`);
    await screen.findByTestId('recipe-ingredients');
    fireEvent.click(screen.getByLabelText('More actions'));
    fireEvent.click(screen.getByLabelText('Delete Chicken Chili'));
    await waitFor(() => expect(m.cookbookDelete).toHaveBeenCalledWith(CHILI.path));
    expect(confirmDialog).toHaveBeenCalled();
  });
});

describe('adding a recipe', () => {
  it('searches, previews and saves with its photo', async () => {
    m.searchRecipes.mockResolvedValue(ok('', { results: [{ title: 'Green Chili', source: 'themealdb', source_url: 'https://x/1', site: 'TheMealDB', image: 'https://img/1.jpg', ingredients: ['1 chili'], instructions: ['Cook.'], prep_minutes: null, cook_minutes: null, total_minutes: null, yield: '', description: '', notes: '', calories: 300, protein_g: 20 }] }));
    m.cookbookSave.mockResolvedValue(ok('saved', { path: 'Recipes/Green-Chili.md', photo: true }));
    renderAt('/recipes?add=1');
    fireEvent.change(await screen.findByLabelText('Search recipes'), { target: { value: 'chili' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    fireEvent.click(await screen.findByText('Green Chili'));
    fireEvent.click(await screen.findByRole('button', { name: /save to the cookbook/i }));
    await waitFor(() =>
      expect(m.cookbookSave).toHaveBeenCalledWith(expect.objectContaining({ title: 'Green Chili', image_url: 'https://img/1.jpg' })),
    );
  });

  it('refuses a name already in the cookbook', async () => {
    m.importRecipe.mockResolvedValue(ok('', { recipe: { title: 'Chicken Chili', source_url: 'https://y', site: 'y', image: null, ingredients: [], instructions: [], prep_minutes: null, cook_minutes: null, total_minutes: null, yield: '', description: '', notes: '' } }));
    renderAt('/recipes?add=1');
    fireEvent.change(await screen.findByLabelText('Recipe link'), { target: { value: 'https://y' } });
    fireEvent.click(screen.getByRole('button', { name: /read the link/i }));
    fireEvent.click(await screen.findByRole('button', { name: /save to the cookbook/i }));
    expect(m.cookbookSave).not.toHaveBeenCalled();
  });

  it('writes one by hand', async () => {
    m.cookbookSave.mockResolvedValue(ok('saved', { path: 'Recipes/Soup.md' }));
    renderAt('/recipes?add=1');
    fireEvent.click(await screen.findByTestId('recipe-write-own'));
    fireEvent.change(await screen.findByLabelText('Recipe name'), { target: { value: 'Soup' } });
    fireEvent.change(screen.getByLabelText('Ingredients'), { target: { value: 'stock\nbones' } });
    fireEvent.click(screen.getByRole('button', { name: /save recipe/i }));
    await waitFor(() => expect(m.cookbookSave).toHaveBeenCalledWith(expect.objectContaining({ title: 'Soup', path: undefined })));
    expect(m.cookbookSave.mock.calls[0][0].content).toContain('- [ ] bones');
  });
});

describe('the week and the shopping list', () => {
  it('shows the week, builds the shopping list and pushes the week to Skylight', async () => {
    renderAt('/recipes?tab=week');
    await waitFor(() => expect(screen.getByTestId('plan-day-Mon')).toHaveTextContent('Chicken Chili'));
    fireEvent.click(screen.getByRole('button', { name: /build shopping list/i }));
    await waitFor(() =>
      expect(m.writeNote).toHaveBeenCalledWith(expect.objectContaining({ title: 'Shopping list', content: expect.stringContaining('- [ ] 2 lb chicken') })),
    );
    fireEvent.click(screen.getByTestId('plan-send-skylight'));
    await waitFor(() => expect(m.pushSkylightWeek).toHaveBeenCalledWith({ days: [expect.objectContaining({ title: 'Chicken Chili' })] }));
  });

  it('picks a dinner for an empty day', async () => {
    renderAt('/recipes?tab=week');
    await waitFor(() => expect(screen.getByTestId('plan-day-Mon')).toHaveTextContent('Chicken Chili'));
    fireEvent.click(screen.getByLabelText('Plan for Tue'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByText('Sugar Cookies'));
    await waitFor(() =>
      expect(m.writeNote).toHaveBeenCalledWith(expect.objectContaining({ title: 'Meal plan', content: expect.stringContaining('Tue · Sugar Cookies') })),
    );
  });

  it('counts a dinner ticked cooked as made that day', async () => {
    m.recipeMade.mockResolvedValue(stat());
    renderAt('/recipes?tab=week');
    fireEvent.click(await screen.findByLabelText('Mon cooked'));
    await waitFor(() => expect(m.recipeMade).toHaveBeenCalledWith(CHILI.path, expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/)));
  });

  it('ticks items off the list and sends what is left to Skylight', async () => {
    m.readNote.mockImplementation(async (title: string) =>
      title === 'Shopping list'
        ? { status: 'SUCCESS', message: '#shopping-list\n\n- [ ] eggs\n- [ ] milk\n', service: 'note_read' }
        : { status: 'FAILURE', message: 'not found', service: 'note_read' },
    );
    renderAt('/recipes?tab=list');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'eggs' }));
    await waitFor(() =>
      expect(m.writeNote).toHaveBeenCalledWith(expect.objectContaining({ title: 'Shopping list', content: expect.stringContaining('- [x] eggs') })),
    );
    fireEvent.click(screen.getByTestId('list-send-skylight'));
    await waitFor(() => expect(m.mirrorSkylightList).toHaveBeenCalledWith({ items: ['milk'] }));
  });
});

describe('the shared cookbook', () => {
  it('lets the owner approve a suggested change', async () => {
    m.cookbookSuggestions.mockResolvedValue([{
      id: 's1', path: 'Recipes/Suggestions/s1.md', recipe: CHILI.path, recipe_title: 'Chicken Chili',
      by: 'taylor', at: '2026-11-01T10:00:00Z', message: 'more beans', content: 'beans', current: 'chicken',
      owner: 'mom', owner_label: 'Mom', mine: false, can_review: true,
    }]);
    m.cookbookResolve.mockResolvedValue(ok('Change approved.'));
    renderAt();
    fireEvent.click(await screen.findByTestId('recipe-suggestions-toggle'));
    expect(screen.getByTestId('recipe-suggestions')).toHaveTextContent('from taylor');
    fireEvent.click(screen.getByRole('button', { name: /approve/i }));
    await waitFor(() => expect(m.cookbookResolve).toHaveBeenCalledWith('Recipes/Suggestions/s1.md', 'approve'));
  });

  it('offers to move recipes still in my own folder', async () => {
    m.cookbookPersonal.mockResolvedValue(['Tacos']);
    m.cookbookAdopt.mockResolvedValue(ok('Moved 1 recipe(s).'));
    renderAt();
    expect(await screen.findByTestId('recipe-personal')).toHaveTextContent('1 of your recipe is');
    fireEvent.click(screen.getByRole('button', { name: /add them to the family cookbook/i }));
    await waitFor(() => expect(m.cookbookAdopt).toHaveBeenCalled());
  });

  it('teaches Jarvis the cookbook from the menu', async () => {
    renderAt();
    fireEvent.click(await screen.findByLabelText('More cookbook actions'));
    fireEvent.click(screen.getByRole('menuitem', { name: /teach jarvis/i }));
    await waitFor(() => expect(m.syncNotesRag).toHaveBeenCalledWith({ cookbook: true }));
  });
});
