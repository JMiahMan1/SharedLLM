import { test, expect, type Page } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';

// The note editor on a phone: every button and line of text stays inside the
// screen (it used to run off the right edge: the colour row, the bottom
// buttons, the save status).
const NOTE = {
  title: 'Grocery list for the weekend with a long title',
  path: 'Notes/Grocery list for the weekend with a long title.md',
  size: 120,
  modified: new Date().toISOString(),
  content: '# Grocery list\n\n- [ ] Milk\n- [ ] Bread\n\nSome text #shopping',
};

async function mockNotes(page: Page) {
  await page.route((url: URL) => url.pathname.startsWith('/api/'), (route) => {
    const url = route.request().url();
    if (url.includes('/api/users/me')) return route.fallback();
    if (url.includes('/notes/list')) return route.fulfill({ json: { status: 'SUCCESS', detail: { notes: [NOTE] } } });
    if (url.includes('/notes/read')) return route.fulfill({ json: { status: 'SUCCESS', message: NOTE.content, detail: { etag: 'e1' } } });
    return route.fulfill({ json: {} });
  });
}

async function overflowing(page: Page) {
  return page.evaluate(() => {
    const editor = document.querySelector('[data-testid="note-editor"]');
    if (!editor) return ['no editor'];
    const limit = window.innerWidth;
    return [...editor.querySelectorAll<HTMLElement>('*')]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.right > limit + 0.5;
      })
      .map((el) => `${el.tagName.toLowerCase()}[${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30)}] right=${Math.round(el.getBoundingClientRect().right)} > ${limit}`);
  });
}

// A narrow phone, and the same phone with Android's font size turned up (the
// app's WebView scales its text with it).
for (const { width, zoom } of [{ width: 412, zoom: 1 }, { width: 360, zoom: 1 }, { width: 360, zoom: 1.3 }]) {
  test(`the note editor fits a ${width}px phone, text at ${zoom * 100}%`, async ({ page }, info) => {
    test.skip((info.project.use.viewport?.width ?? 1000) > 500, 'phone layout only');
    await page.setViewportSize({ width, height: 800 });
    if (zoom !== 1) {
      await page.addInitScript((z) => {
        document.addEventListener('DOMContentLoaded', () => {
          document.documentElement.style.fontSize = `${16 * z}px`;
          document.documentElement.style.setProperty('-webkit-text-size-adjust', `${z * 100}%`);
        });
      }, zoom);
    }
    await mockNotes(page);
    await seedAuth(page);
    await page.goto('/notes');
    await page.getByText(NOTE.title).first().click();
    await expect(page.getByTestId('note-editor')).toBeVisible();
    await page.waitForTimeout(800);
    expect(await overflowing(page)).toEqual([]);
    // The panels the bottom buttons open fit too.
    for (const name of ['Share', 'Remind me', 'History']) {
      const button = page.getByTestId('note-editor').getByRole('button', { name, exact: false }).first();
      if (await button.count()) {
        await button.click();
        await page.waitForTimeout(400);
        expect(await overflowing(page), `with ${name} open`).toEqual([]);
        await button.click();
      }
    }
    await page.screenshot({ path: `../../.tmp/notes-editor-${width}-${zoom}.png`, fullPage: false });
  });
}
