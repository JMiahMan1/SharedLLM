import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';
import { controlsOffScreen, expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The dashboard's note and picture widgets (Home Assistant's markdown and
 * picture cards) in every project. Only those two are on the board; every
 * other dashboard call gets an empty answer, as in the weather spec. The
 * widget saves are recorded, and the only picture loaded is the mocked one.
 */

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');

async function board(page: Page) {
  const errors: string[] = [];
  const saves: Array<{ key: string; body: unknown }> = [];
  const pictures: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await seedAuth(page);
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/'), (route: Route) => {
    const req = route.request();
    if (req.method() !== 'GET') return route.fulfill({ json: { status: 'SUCCESS' } });
    return route.fulfill({ json: [] });
  });
  await page.route('**/api/users/me', (route) => route.fulfill({ json: {
    id: 1, username: 'parent', display_name: 'Parent', is_admin: true, role: 'admin' } }));
  await page.route('**/api/widgets/settings', (route) => route.fulfill({ json: {
    widgets: ['note', 'picture'].map((key, i) => ({
      widget_key: key, visibility: 'visible', order_index: i, size: 'medium', is_pinned: false, sort_mode: null,
      pinned_devices: [], config: key === 'note'
        ? { title: 'House rules', markdown: '- **Shoes** off\n- [Wi-Fi help](https://help.example.test)\n\n<script>window.bad=1</script>' }
        : {}, updated_at: 0,
    })),
    quick_assistant_enabled: false,
  } }));
  await page.route('**/api/widgets/settings/*', async (route) => {
    const key = new URL(route.request().url()).pathname.split('/').pop() ?? '';
    saves.push({ key, body: route.request().postDataJSON() });
    return route.fulfill({ json: { status: 'SUCCESS' } });
  });
  await page.route('https://photos.example.test/**', (route) => {
    pictures.push(route.request().url());
    return route.fulfill({ contentType: 'image/png', body: PNG });
  });
  await page.goto('/');
  await expect(page.locator('[data-widget="note"]')).toBeVisible();
  return { errors, saves, pictures };
}

test.describe('note and picture widgets', () => {
  test.describe.configure({ timeout: 90_000 });

  test('a note in markdown, safely drawn, edited in place', async ({ page }, info) => {
    const run = await board(page);
    const note = page.locator('[data-widget="note"]');
    await expect(note.getByText('House rules')).toBeVisible();
    await expect(note.locator('strong', { hasText: 'Shoes' })).toBeVisible();
    await expect(note.getByRole('link', { name: 'Wi-Fi help' })).toHaveAttribute('rel', 'noopener noreferrer');
    expect(await page.evaluate(() => (window as unknown as { bad?: number }).bad)).toBeUndefined();
    await expect(note.locator('script')).toHaveCount(0);

    await note.getByRole('button', { name: 'Edit note' }).click();
    await expectTapTargets(note.getByTestId('note-editor'));
    await note.getByRole('textbox', { name: 'Note text (markdown)' }).fill('## Bins\nOut on **Tuesday**');
    await note.getByRole('button', { name: 'Save' }).click();
    await expect(note.getByRole('heading', { name: 'Bins' })).toBeVisible();
    expect(run.saves.filter((x) => x.key === 'note')).toEqual([{ key: 'note', body: { config: { title: 'House rules', markdown: '## Bins\nOut on **Tuesday**' } } }]);
    await expectNoHorizontalOverflow(page);
    expect(await controlsOffScreen(page)).toEqual([]);
    await snap(page, info, 'note-widget');
    expect(run.errors).toEqual([]);
  });

  test('a picture from an https link; anything else is refused', async ({ page }, info) => {
    const run = await board(page);
    const pic = page.locator('[data-widget="picture"]');
    await pic.getByRole('button', { name: 'Choose a picture' }).click();
    const editor = pic.getByTestId('picture-editor');
    await expectTapTargets(editor);
    await editor.getByRole('textbox', { name: 'Picture link' }).fill('javascript:alert(1)');
    await expect(editor.getByRole('alert')).toContainText('https://');
    await expect(editor.getByRole('button', { name: 'Save' })).toBeDisabled();
    await editor.getByRole('textbox', { name: 'Picture link' }).fill('https://photos.example.test/garden.png');
    await editor.getByRole('textbox', { name: 'Caption' }).fill('Garden');
    await editor.getByRole('button', { name: 'Save' }).click();
    const img = pic.getByTestId('picture-image');
    await expect(img).toBeVisible();
    await expect(img).toHaveAttribute('referrerpolicy', 'no-referrer');
    await expect(pic.getByText('Garden')).toBeVisible();
    expect(run.saves.filter((x) => x.key === 'picture')).toEqual([{ key: 'picture', body: { config: {
      src: 'https://photos.example.test/garden.png', caption: 'Garden', alt: '' } } }]);
    expect(run.pictures).toEqual(['https://photos.example.test/garden.png']);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, 'picture-widget');
    expect(run.errors).toEqual([]);
  });
});

