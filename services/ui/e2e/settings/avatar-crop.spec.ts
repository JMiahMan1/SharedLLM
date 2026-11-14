import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome, PROFILES } from '../fixtures/homeMocks';
import { controlsOffScreen, expectFits, expectNoHorizontalOverflow, expectTapTargets } from '../fixtures/layoutChecks';

/**
 * Cropping a profile photo, on every form factor: the cropper fits the
 * screen with every button reachable, dragging and zooming change what is
 * sent, the original and crop go with the photo, and "Adjust photo" reopens
 * the kept original where it was left. The photo is drawn in the test.
 */

const expect = baseExpect.configure({ timeout: 15_000 });

interface Saved { data_url: string; original_url?: string; crop?: { x: number; y: number; zoom: number } }

async function setUp(page: Page) {
  await mockHome(page, 'admin');
  const uploads: Saved[] = [];
  let avatar: string | null = null;
  let original: string | null = null;
  let crop: Saved['crop'] | null = null;
  const me = () => ({ ...PROFILES.admin, avatar });
  // Registered after the shared mocks, so these answer first. The rest of the Identity page:
  await page.route('**/api/spotify/status', (route) => route.fulfill({ json: { configured: false, missing: ['SPOTIFY_CLIENT_ID'], linked: false } }));
  await page.route('**/api/users/me/voice', (route) => route.fulfill({
    json: { enrolled: false, samples: 0, min_samples: 3, max_samples: 10, consistency: null, model: 'test', updated_at: null } }));
  await page.route('**/api/user-panel/devices', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/users/me/keys', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/users/me', (route) => route.fulfill({ json: me() }));
  await page.route('**/api/users/me/avatar/image', async (route) => {
    const body = route.request().postDataJSON() as Saved;
    uploads.push(body);
    if (body.original_url) original = body.original_url;
    crop = body.crop ?? null;
    avatar = `upload:${uploads.length}`;
    await route.fulfill({ json: me() });
  });
  await page.route('**/api/users/me/avatar/source', (route) => route.fulfill({
    json: { original_url: original, crop, kept_original: Boolean(original) },
  }));
  await page.route('**/api/users/*/avatar/image', (route) => (route.request().method() !== 'GET' ? route.fallback() : route.fulfill({ contentType: 'image/png', body: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64') })));
  return uploads;
}

/** A 1200 x 800 photo with a red dot near its top left, made in the page. */
async function photo(page: Page): Promise<Buffer> {
  const dataUrl = await page.evaluate(() => {
    const c = document.createElement('canvas');
    c.width = 1200;
    c.height = 800;
    const g = c.getContext('2d')!;
    const grad = g.createLinearGradient(0, 0, 1200, 800);
    grad.addColorStop(0, '#1e3a8a');
    grad.addColorStop(1, '#f59e0b');
    g.fillStyle = grad;
    g.fillRect(0, 0, 1200, 800);
    g.fillStyle = '#ef4444';
    g.beginPath();
    g.arc(250, 220, 120, 0, Math.PI * 2);
    g.fill();
    return c.toDataURL('image/png');
  });
  return Buffer.from(dataUrl.split(',')[1], 'base64');
}

async function openPicker(page: Page) {
  await page.getByTestId('avatar-open').click();
  await expect(page.getByRole('dialog', { name: 'Choose a profile picture' })).toBeVisible();
}

test.describe('profile photo crop', () => {
  test.describe.configure({ timeout: 90_000 });

  test('a new photo opens in the cropper, which fits the screen, and its crop is what is saved', async ({ page }) => {
    const uploads = await setUp(page);
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('/identity');
    await expect(page.getByTestId('avatar-open')).toBeVisible();
    expect(await controlsOffScreen(page), 'the Identity page').toEqual([]);
    await openPicker(page);
    expect(await controlsOffScreen(page), 'the picture picker').toEqual([]);
    await page.getByTestId('avatar-file').setInputFiles({ name: 'me.png', mimeType: 'image/png', buffer: await photo(page) });

    const cropper = page.getByRole('dialog', { name: 'Crop your photo' });
    await expect(cropper).toBeVisible();
    const sheet = cropper.locator(':scope > div').last();
    await expectFits(page, sheet);
    await expectNoHorizontalOverflow(page);
    await expectTapTargets(sheet);
    await expect(cropper.getByTestId('avatar-crop-save')).toBeInViewport();
    await expect(cropper.getByTestId('avatar-crop-previews')).toBeVisible();
    expect(await controlsOffScreen(page), 'the cropper').toEqual([]);

    // Zoom in, then drag the photo to the right and down: the square moves toward the red dot.
    await cropper.getByRole('button', { name: 'Zoom in' }).click();
    await cropper.getByRole('button', { name: 'Zoom in' }).click();
    const view = cropper.getByTestId('avatar-crop-view');
    const box = (await view.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 + 40, { steps: 5 });
    await page.mouse.up();

    await cropper.getByTestId('avatar-crop-save').click();
    await expect(cropper).toBeHidden();
    expect(uploads).toHaveLength(1);
    const sent = uploads[0];
    expect(sent.data_url.startsWith('data:image/jpeg;base64,')).toBe(true);
    expect(sent.original_url?.startsWith('data:image/jpeg;base64,')).toBe(true);
    expect(sent.crop!.zoom).toBeCloseTo(1.5);
    expect(sent.crop!.x).toBeLessThan(0.5);
    expect(sent.crop!.y).toBeLessThan(0.5);
    expect(errors).toEqual([]);
  });

  test('Adjust photo reopens the kept original where it was left, and sends only the new crop', async ({ page }) => {
    const uploads = await setUp(page);
    await page.goto('/identity');
    await openPicker(page);
    await page.getByTestId('avatar-file').setInputFiles({ name: 'me.png', mimeType: 'image/png', buffer: await photo(page) });
    await page.getByTestId('avatar-crop-zoom').fill('2');
    await page.getByTestId('avatar-crop-save').click();
    await expect(page.getByRole('dialog', { name: 'Crop your photo' })).toBeHidden();

    const picker = page.getByRole('dialog', { name: 'Choose a profile picture' });
    await expectTapTargets(picker.getByTestId('avatar-adjust'));
    await picker.getByTestId('avatar-adjust').click();
    const cropper = page.getByRole('dialog', { name: 'Crop your photo' });
    await expect(cropper.getByTestId('avatar-crop-zoom')).toHaveValue('2');
    await cropper.getByRole('button', { name: 'Reset the crop' }).click();
    await cropper.getByTestId('avatar-crop-save').click();
    await expect(cropper).toBeHidden();
    expect(uploads).toHaveLength(2);
    expect(uploads[1].original_url).toBeUndefined();
    expect(uploads[1].crop).toEqual({ x: 0.5, y: 0.5, zoom: 1 });
  });
});
