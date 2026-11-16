import { test, expect as baseExpect, type Page } from '@playwright/test';
import { mockHome } from '../fixtures/homeMocks';
import { expectNoHorizontalOverflow, expectTapTargets, snap } from '../fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * The recordings timeline on the Cameras page, at every size: a busy day (150
 * recordings of every type) is a horizontal track under the player, not a list
 * that pushes the player off the screen. Hermetic: homeMocks answers everything,
 * and the day's recordings are made up here, spread over whatever part of today
 * has passed so the test works at any hour.
 */

const TYPES: string[][] = [['person', 'motion'], ['vehicle'], ['animal'], ['motion'], ['timer']];

async function busyDay(page: Page, count = 150) {
  await page.route((url) => /\/api\/cameras\/[^/]+\/recordings$/.test(url.pathname), (route) => {
    const url = new URL(route.request().url());
    const start = Date.parse(url.searchParams.get('start') ?? '');
    const end = Date.parse(url.searchParams.get('end') ?? '');
    const gap = (end - start) / count;
    const recordings = Array.from({ length: count }, (_, i) => {
      const at = start + i * gap + gap * 0.2;
      // About half the gap long (at most a minute), so neighbours never touch when zoomed in.
      const seconds = Math.max(2, Math.min(60, Math.floor((gap * 0.5) / 1000)));
      return {
        file: `clip-${String(i).padStart(3, '0')}.mp4`,
        start: new Date(at).toISOString(),
        end: new Date(at + seconds * 1000).toISOString(),
        duration: seconds,
        triggers: TYPES[i % TYPES.length],
      };
    });
    return route.fulfill({ json: { recordings } });
  });
}

async function openRecordings(page: Page) {
  const mocks = await mockHome(page, 'admin');
  await busyDay(page);
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/cameras?camera=camera.yard&view=recordings');
  const viewer = page.getByRole('dialog', { name: 'Yard' });
  const track = viewer.getByTestId('timeline-track');
  await expect(track).toBeVisible();
  return { mocks, errors, viewer, track };
}

const playingFile = async (page: Page) => {
  const src = await page.getByRole('dialog', { name: 'Yard' }).locator('video').getAttribute('src');
  return src ? new URL(src).searchParams.get('file') : null;
};

test.describe('camera recordings timeline', () => {
  test.describe.configure({ timeout: 90_000 });

  test('a busy day fits on the screen under the player, with no list in the way', async ({ page }, info) => {
    const { viewer, track, errors, mocks } = await openRecordings(page);
    // The list is folded away; the track, its controls and the type chips are on screen without scrolling.
    await expect(viewer.getByRole('list', { name: 'Recordings' })).toHaveCount(0);
    await expect(viewer.getByText('List (150)')).toBeVisible();
    const height = page.viewportSize()!.height;
    for (const part of [viewer.getByTestId('recording-placeholder'), track, viewer.getByRole('group', { name: 'Zoom' }),
      viewer.getByTestId('timeline-filter-all')]) {
      const box = (await part.boundingBox())!;
      expect(box.y + box.height, 'on screen without scrolling').toBeLessThanOrEqual(height);
    }
    // The dialog does not grow with the number of recordings.
    const scroll = await viewer.evaluate((el) => el.scrollHeight);
    expect(scroll).toBeLessThan(height + 900);
    await expect(track).toHaveAttribute('aria-valuetext', /\d/);
    await expect(viewer.getByTestId('timeline-now')).toHaveCount(1);
    await expectNoHorizontalOverflow(page);
    await expectTapTargets(viewer.getByRole('group', { name: 'Zoom' }));
    await expectTapTargets(viewer.getByRole('group', { name: 'Show recordings of type' }));
    await snap(page, info, 'camera-timeline');
    // A whole day of 150 clips is drawn as merged blocks, not 150 elements.
    const blocks = await viewer.getByTestId('timeline-block').count();
    expect(blocks).toBeGreaterThan(0);
    expect(blocks).toBeLessThan(150);
    expect(errors).toEqual([]);
    expect(mocks.unmocked).toEqual([]);
  });

  test('tapping a clip plays it, and the next clip follows when it ends', async ({ page }, info) => {
    const { viewer, track } = await openRecordings(page);
    await viewer.getByTestId('timeline-zoom-1h').click();
    await expect(viewer.getByTestId('timeline-zoom-1h')).toHaveAttribute('aria-pressed', 'true');
    const blocks = viewer.getByTestId('timeline-block');
    await expect(blocks.first()).toBeVisible();
    // A clip away from the playhead, fully on the track.
    const trackBox = (await track.boundingBox())!;
    const candidates = await blocks.evaluateAll((els) => els.map((el) => {
      const b = el.getBoundingClientRect();
      // Just before the clip's start: within a finger's width, so it plays from its first second.
      return { file: el.getAttribute('data-file'), x: b.x - 2, y: b.y + b.height / 2, w: b.width };
    }));
    const pick = candidates.find((c) => c.x > trackBox.x + 20 && c.x < trackBox.x + trackBox.width / 2 - 30);
    expect(pick, 'a clip left of the playhead').toBeTruthy();
    await page.mouse.click(pick!.x, pick!.y);
    await expect.poll(() => playingFile(page)).toBe(pick!.file);
    await expect(viewer.locator('video')).toBeVisible();
    await expect(track).not.toHaveAttribute('aria-valuetext', /no recording/);
    await snap(page, info, 'camera-timeline-playing');
    // The test video is 2 s long: when it ends the next clip plays on its own.
    const n = Number(pick!.file!.slice(5, 8));
    const following = `clip-${String(n + 1).padStart(3, '0')}.mp4`;
    await expect.poll(() => playingFile(page), { timeout: 15_000 }).toBe(following);
    await expectNoHorizontalOverflow(page);
  });

  test('type chips filter the track and the jumps', async ({ page }, info) => {
    const { viewer } = await openRecordings(page);
    for (const kind of ['person', 'vehicle', 'animal', 'motion', 'continuous']) {
      await expect(viewer.getByTestId(`timeline-filter-${kind}`)).toBeVisible();
    }
    await expect(viewer.getByTestId('timeline-filter-package')).toHaveCount(0);
    await viewer.getByTestId('timeline-filter-vehicle').click();
    await expect(viewer.getByTestId('timeline-filter-vehicle')).toHaveAttribute('aria-pressed', 'true');
    await expect(viewer.getByText('List (30)')).toBeVisible();
    const kinds = await viewer.getByTestId('timeline-block').evaluateAll((els) => [...new Set(els.map((e) => e.getAttribute('data-kind')))]);
    expect(kinds).toEqual(['vehicle']);
    // Previous jumps to a vehicle clip only.
    await viewer.getByRole('button', { name: 'Previous recording' }).click();
    const file = await (async () => { await expect.poll(() => playingFile(page)).not.toBeNull(); return playingFile(page); })();
    expect(Number(file!.slice(5, 8)) % 5).toBe(1);
    await snap(page, info, 'camera-timeline-filtered');
    // The list (opened) holds the same 30.
    await viewer.getByText('List (30)').click();
    await expect(viewer.getByRole('list', { name: 'Recordings' }).getByRole('listitem')).toHaveCount(30);
    await viewer.getByTestId('timeline-filter-all').click();
    await expect(viewer.getByText('List (150)')).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test('zoom buttons, keys and ctrl+wheel change the span; dragging moves through the day', async ({ page }) => {
    const { viewer, track } = await openRecordings(page);
    const pressed = (id: string) => expect(viewer.getByTestId(`timeline-zoom-${id}`)).toHaveAttribute('aria-pressed', 'true');
    await pressed('24h');
    const blocksAt = () => viewer.getByTestId('timeline-block').count();
    const wide = await blocksAt();
    await viewer.getByTestId('timeline-zoom-10m').click();
    await pressed('10m');
    // Ten minutes around the playhead hold a clip or two, not the day.
    await expect.poll(blocksAt).toBeLessThan(Math.max(wide, 5));
    await track.focus();
    await page.keyboard.press('-');
    await pressed('1h');
    await page.keyboard.press('-');
    await pressed('6h');
    await page.keyboard.press('+');
    await pressed('1h');
    // Ctrl + wheel out: the span grows past an hour (no preset is pressed until it lands on one).
    const box = (await track.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.keyboard.down('Control');
    await page.mouse.wheel(0, 120);
    await page.keyboard.up('Control');
    await expect(viewer.getByTestId('timeline-zoom-1h')).toHaveAttribute('aria-pressed', 'false');
    // Arrow keys move the playhead; dragging the track left brings a later time under it.
    await viewer.getByTestId('timeline-zoom-6h').click();
    const before = Number(await track.getAttribute('aria-valuenow'));
    await track.focus();
    await page.keyboard.press('ArrowLeft');
    await expect.poll(async () => Number(await track.getAttribute('aria-valuenow'))).toBeLessThan(before);
    const afterKey = Number(await track.getAttribute('aria-valuenow'));
    await page.mouse.move(box.x + box.width * 0.75, box.y + 10);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.5, box.y + 10, { steps: 8 });
    await page.mouse.up();
    await expect.poll(async () => Number(await track.getAttribute('aria-valuenow'))).toBeGreaterThan(afterKey);
    // Home plays the day's first clip, Page Down the one after it.
    await track.focus();
    await page.keyboard.press('Home');
    await expect.poll(() => playingFile(page)).toBe('clip-000.mp4');
    // (The 2 s test video may already have run on into the next clip: Page Down goes on from wherever it is.)
    const index = async () => Number((await playingFile(page))?.slice(5, 8));
    const from = await index();
    await page.keyboard.press('PageDown');
    await expect.poll(index).toBeGreaterThan(from);
    await expectNoHorizontalOverflow(page);
  });
});
