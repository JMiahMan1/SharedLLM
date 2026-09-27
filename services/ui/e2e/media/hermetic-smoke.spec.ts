import { test, expect } from '@playwright/test';
import { seedAuth, mockMediaApi } from '../fixtures/mediaMocks';

test.describe('media hermetic smoke', () => {
  test('loads /media with mocks and shows the heading', async ({ page }) => {
    const mocks = await mockMediaApi(page, 'happy');
    await seedAuth(page);

    await page.goto('/media');

    await expect(page.getByRole('heading', { name: 'Media', level: 1 })).toBeVisible();

    mocks.assertNoUnmocked();
  });
});
