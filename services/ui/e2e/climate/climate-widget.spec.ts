import { test, expect, type Page, type Locator } from '@playwright/test';
import { mockClimateApi, shotPath, type ClimateMocks, type ClimateScenario } from '../fixtures/climateMocks';

const ALL_ROOMS = [
  'climate.living_room', 'climate.kitchen', 'climate.bedroom', 'climate.nursery', 'climate.office',
];

async function boot(page: Page, scenario: ClimateScenario): Promise<ClimateMocks> {
  const mocks = await mockClimateApi(page, scenario);
  // A widget that throws is caught by WidgetErrorBoundary and shows "Widget
  // failed", so the page still looks fine — surface the real error instead.
  page.on('pageerror', (e) => console.log('[pageerror]', e.message));
  await page.goto('/');
  await expect(page.getByText('Widget failed')).toHaveCount(0);
  return mocks;
}

/** The bento cell that hosts the widget — the real box a thumb has to live in. */
function cell(page: Page): Locator {
  return page.locator('div.grid.gap-5 > div').first();
}

interface Box { x: number; y: number; w: number; h: number }

interface Metrics {
  coarse: boolean;
  viewport: { w: number; h: number };
  docScrollW: number;
  clientW: number;
  card: Box;
  cardScrollW: number;
  ring: Box | null;
  /** >0 means the widget body needs internal scrolling inside its band. */
  bodyOverflow: number;
  strip: { box: Box; scrollW: number; clientW: number; touchAction: string } | null;
  controls: Array<{ t: string; w: number; h: number }>;
}

async function metrics(page: Page, testId: string): Promise<Metrics> {
  return page.evaluate((id) => {
    const el = document.querySelector(`[data-testid="${id}"]`);
    if (!el) throw new Error(`${id} never rendered`);
    const card = (el.closest('div.grid > div') || el) as HTMLElement;
    const box = (n: Element): Box => {
      const b = n.getBoundingClientRect();
      return { x: b.x, y: b.y, w: b.width, h: b.height };
    };
    const ring = el.querySelector('[data-testid="climate-ring"]');
    const strip = el.querySelector('[class~="overflow-x-auto"]');
    const controls = Array.from(
      card.querySelectorAll<HTMLElement>('button, select, input, [role="radio"], [role="checkbox"]'),
    );
    return {
      coarse: window.matchMedia('(pointer: coarse)').matches,
      viewport: { w: window.innerWidth, h: window.innerHeight },
      docScrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth,
      card: box(card),
      cardScrollW: card.scrollWidth,
      ring: ring ? box(ring) : null,
      bodyOverflow: (() => {
        for (let n = el.parentElement; n && n !== card; n = n.parentElement) {
          const s = getComputedStyle(n);
          if (/(auto|scroll)/.test(s.overflowY)) return Math.round(n.scrollHeight - n.clientHeight);
        }
        return 0;
      })(),
      strip: strip
        ? {
            box: box(strip), scrollW: strip.scrollWidth, clientW: strip.clientWidth,
            touchAction: getComputedStyle(strip).touchAction,
          }
        : null,
      controls: controls.map((c) => {
        const b = c.getBoundingClientRect();
        return {
          t: (c.textContent || c.getAttribute('aria-label') || c.tagName).trim().slice(0, 14),
          w: Math.round(b.width),
          h: Math.round(b.height),
        };
      }),
    };
  }, testId);
}

/**
 * A real finger, not a mouse. Raw touch events do not drive Chromium's scroll
 * pipeline, so this uses the compositor's own gesture synthesiser, which
 * hit-tests the start point and obeys touch-action exactly like a swipe.
 */
/**
 * Drag with a real touch sequence.
 *
 * `Input.synthesizeScrollGesture` does not scroll in this headless shell at
 * all — verified against a bare 4000px page, where every speed/scrollType
 * combination left scrollTop at 0. It "passes" a horizontal assertion only
 * because the target element scrolls itself. A hand-rolled
 * touchStart/touchMove/touchEnd sequence does scroll, so it is the only way
 * to assert that a vertical drag reaches the ancestor scroller.
 */
async function touchDrag(page: Page, from: { x: number; y: number }, distance: { x: number; y: number }): Promise<void> {
  const cdp = await page.context().newCDPSession(page);
  const steps = 8;
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: Math.round(from.x), y: Math.round(from.y) }],
  });
  for (let i = 1; i <= steps; i++) {
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{
        x: Math.round(from.x + (distance.x * i) / steps),
        y: Math.round(from.y + (distance.y * i) / steps),
      }],
    });
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForTimeout(250);
}

test.describe('climate widget at phone resolution', () => {
  test('single-device dial fits the column with thumb-sized targets', async ({ page }, info) => {
    const mocks = await boot(page, { devices: ['climate.living_room'] });
    const dial = page.getByTestId('climate-dial');
    await expect(dial).toBeVisible();
    await expect(dial).toContainText('22');

    const m = await metrics(page, 'climate-dial');
    console.log(`${info.project.name}: coarse=${m.coarse} viewport=${m.viewport.w}x${m.viewport.h} card=${Math.round(m.card.w)}x${Math.round(m.card.h)}`);
    console.log(`sideways overflow: ${m.docScrollW - m.clientW}px; card overflow: ${m.cardScrollW - Math.round(m.card.w)}px; ring ${Math.round(m.ring?.w || 0)}x${Math.round(m.ring?.h || 0)}`);

    expect(m.docScrollW, 'page must not scroll sideways').toBeLessThanOrEqual(m.clientW);
    expect(m.cardScrollW, 'nothing may overflow the card').toBeLessThanOrEqual(Math.ceil(m.card.w));
    expect(m.ring!.w, 'ring fits the card').toBeLessThanOrEqual(m.card.w + 1);
    expect(Math.abs(m.ring!.w - m.ring!.h), 'ring stays circular').toBeLessThanOrEqual(2);

    const small = m.controls.filter((c) => c.h < 43.5);
    console.log(`controls=${m.controls.length}; under 44px: ${small.map((c) => `${c.t}=${c.h}`).join(', ') || 'none'}`);
    console.log(`stubbed endpoints: ${[...new Set(mocks.unmocked)].sort().join(', ') || 'none'}`);
    await page.screenshot({ path: shotPath(`${info.project.name}-01-dial`) });
    await cell(page).screenshot({ path: shotPath(`${info.project.name}-02-dial-card`) });
    if (m.coarse) expect(small, 'thumb targets').toEqual([]);
  });

  test('multi-device tiles promote into a dial on tap', async ({ page }, info) => {
    await boot(page, { devices: ALL_ROOMS });
    const tiles = page.getByTestId('climate-tile');
    await expect(tiles).toHaveCount(ALL_ROOMS.length);

    const m = await metrics(page, 'climate-tile');
    expect(m.docScrollW).toBeLessThanOrEqual(m.clientW);
    expect(m.cardScrollW, 'tile grid must not push past the card').toBeLessThanOrEqual(Math.ceil(m.card.w));
    const small = m.controls.filter((c) => c.h < 43.5);
    console.log(`under 44px: ${small.map((c) => `${c.t}=${c.h}`).join(', ') || 'none'}`);
    await page.screenshot({ path: shotPath(`${info.project.name}-03-tiles`) });
    if (m.coarse) expect(small, 'thumb targets').toEqual([]);

    // Kitchen is the dual-bound thermostat: its dial carries both bounds.
    await tiles.filter({ hasText: 'Kitchen' }).first().click();
    const dial = page.getByTestId('climate-dial');
    await expect(dial).toHaveAttribute('data-entity', 'climate.kitchen');
    await expect(dial).toContainText('Heat');
    await expect(dial).toContainText('Cool');
    await page.screenshot({ path: shotPath(`${info.project.name}-04-tile-dial`) });

    await page.getByRole('button', { name: 'All rooms' }).click();
    await expect(tiles).toHaveCount(ALL_ROOMS.length);
  });

  test('Devices sheet is a thumb-reachable bottom sheet', async ({ page }, info) => {
    await boot(page, { devices: ALL_ROOMS });
    await page.getByRole('button', { name: 'Devices' }).first().click();
    const sheet = page.getByRole('dialog', { name: 'Climate widget devices' });
    await expect(sheet).toBeVisible();

    const rect = await sheet.evaluate((el) => {
      const b = el.getBoundingClientRect();
      // A fixed element is viewport-fixed only if nothing turns an ancestor into
      // a containing block (transform/filter/backdrop-filter/contain/will-change).
      // A full-viewport fixed backdrop is fine; a card-sized one is not.
      let capturedBy: { who: string; w: number; h: number } | null = null;
      for (let node = el.parentElement; node; node = node.parentElement) {
        const s = getComputedStyle(node);
        const creates = s.transform !== 'none' || s.filter !== 'none' || s.backdropFilter !== 'none'
          || s.perspective !== 'none' || s.containerType !== 'normal'
          || (s.willChange && s.willChange !== 'auto') || /paint|layout|strict|content/.test(s.contain);
        if (creates) {
          const r = node.getBoundingClientRect();
          capturedBy = {
            who: `${node.tagName}.${(node.className || '').toString().split(' ').slice(0, 3).join('.')}`,
            w: Math.round(r.width), h: Math.round(r.height),
          };
          break;
        }
      }
      return {
        x: b.x, y: b.y, width: b.width, height: b.height,
        pad: parseFloat(getComputedStyle(el).paddingBottom),
        vw: window.innerWidth, vh: window.innerHeight, capturedBy,
      };
    });
    const vh = page.viewportSize()!.height;
    console.log(`sheet ${Math.round(rect.width)}x${Math.round(rect.height)} at y=${Math.round(rect.y)} (vh ${vh}), safe-area pad ${rect.pad}px`);
    console.log(`fixed containing block: ${rect.capturedBy ? `${rect.capturedBy.who} ${rect.capturedBy.w}x${rect.capturedBy.h} (viewport ${rect.vw}x${rect.vh})` : 'viewport'}`);
    await page.screenshot({ path: shotPath(`${info.project.name}-05-setup`) });

    expect(rect.width, 'sheet spans the phone width').toBeLessThanOrEqual(412);
    expect(Math.abs(rect.y + rect.height - vh), 'sheet hugs the bottom edge').toBeLessThanOrEqual(2);
    if (rect.capturedBy) {
      // Only acceptable if whatever captures the sheet is itself the viewport.
      expect(rect.capturedBy.w, 'containing block width').toBeCloseTo(rect.vw, 0);
      expect(rect.capturedBy.h, 'containing block height').toBeCloseTo(rect.vh, 0);
    }

    const small = await sheet.evaluate((el) => Array.from(el.querySelectorAll<HTMLElement>('button, input, [role="checkbox"]'))
      .map((c) => ({ t: (c.textContent || c.getAttribute('aria-label') || '').trim().slice(0, 12), h: Math.round(c.getBoundingClientRect().height) }))
      .filter((c) => c.h < 43.5));
    console.log(`sheet controls under 44px: ${small.map((c) => `${c.t}=${c.h}`).join(', ') || 'none'}`);
    expect(small, 'sheet thumb targets').toEqual([]);

    await sheet.getByRole('radio', { name: 'Dial' }).click();
    await sheet.getByRole('checkbox', { name: /Living Room/i }).click();
    await sheet.getByRole('checkbox', { name: /Kitchen/i }).click();
    await sheet.getByRole('button', { name: 'Save' }).click();
    await expect(sheet).toBeHidden();
    await expect(page.getByTestId('climate-dial')).toBeVisible();
  });

  test('mode strip swipes sideways and still lets the page scroll', async ({ page }, info) => {
    await page.setViewportSize({ width: 360, height: 568 });
    // 'tall' makes the card taller than the phone screen, so the dashboard
    // really does scroll — that is the case the strip must not swallow.
    await boot(page, { devices: ['climate.living_room'], size: 'tall' });
    const dial = page.getByTestId('climate-dial');
    await expect(dial).toBeVisible();

    const strip = page.locator('[data-testid="climate-dial"] [class~="overflow-x-auto"]').first();
    await expect(strip).toBeVisible();
    // A touch drag can only start on pixels that are actually on screen.
    await strip.scrollIntoViewIfNeeded();
    const before = await strip.evaluate((el) => ({
      scrollW: el.scrollWidth, clientW: el.clientWidth, touchAction: getComputedStyle(el).touchAction,
    }));
    const box = (await strip.boundingBox())!;
    const cy = box.y + box.height / 2;

    const start = { x: box.x + box.width / 2, y: cy };
    // Both ends must be reachable with one thumb: fling left from the near end,
    // then right from the far end.
    await strip.evaluate((el) => { el.scrollLeft = 0; });
    await touchDrag(page, start, { x: -240, y: 0 });
    const forward = await strip.evaluate((el) => el.scrollLeft);
    const max = await strip.evaluate((el) => { el.scrollLeft = el.scrollWidth; return el.scrollLeft; });
    await touchDrag(page, start, { x: 240, y: 0 });
    const back = await strip.evaluate((el) => el.scrollLeft);
    console.log(`strip touch-action=${before.touchAction} ${before.scrollW}/${before.clientW}px: left-swipe -> ${forward}, right-swipe from far end (${max}) -> ${back}`);

    const marked = await strip.evaluate((el) => {
      for (let n = el.parentElement; n; n = n.parentElement) {
        const s = getComputedStyle(n);
        if (/(auto|scroll)/.test(s.overflowY) && n.scrollHeight > n.clientHeight + 4) {
          n.scrollTop = 0;
          n.dataset.probeScroll = '1';
          return `${n.tagName}.${(n.className || '').toString().split(' ').slice(0, 2).join('.')} max=${n.scrollHeight - n.clientHeight}`;
        }
      }
      const de = document.scrollingElement as HTMLElement;
      if (de && de.scrollHeight > de.clientHeight + 4) {
        de.scrollTop = 0;
        de.dataset.probeScroll = '1';
        return `document max=${de.scrollHeight - de.clientHeight}`;
      }
      return null;
    });
    let after = -1;
    if (marked) {
      // A vertical scroll aimed at the dashboard while the pointer is over the
      // strip must move the ancestor scroller, not the strip. Wheel is used
      // rather than a synthesised touch drag: this headless shell will not
      // produce a touch scroll at all (verified against a bare 4000px page),
      // and the widget's card is `overflow-hidden`, so only wheel exercises the
      // same ancestor-chaining path that a real finger would take.
      await page.mouse.move(start.x, start.y);
      await page.mouse.wheel(0, 300);
      await page.waitForTimeout(250);
      after = await page.evaluate(() => {
        const el = document.querySelector('[data-probe-scroll="1"]') as HTMLElement | null;
        return el ? el.scrollTop : -1;
      });
    }
    console.log(`scroller=${marked ?? 'none (page fits)'} scrollTopAfterVerticalScroll=${after}`);
    expect(Math.max(forward, max - back), 'mode strip must swipe sideways').toBeGreaterThan(0);
    if (marked) {
      expect(after, 'a vertical scroll over the strip must scroll the dashboard').toBeGreaterThan(0);
    }
    // The strip must not claim the vertical axis, or a thumb dragging down the
    // mode list would scroll nothing at all on a phone.
    expect(before.touchAction, 'strip must claim only the horizontal axis').toBe('pan-x');
    await page.screenshot({ path: shotPath(`${info.project.name}-06-strip-scroll`) });
  });

  test('rapid taps coalesce into one service call', async ({ page }, info) => {
    const mocks = await (async () => {
      const m = await mockClimateApi(page, { devices: ['climate.living_room'] });
      await page.goto('/');
      return m;
    })();
    const dial = page.getByTestId('climate-dial');
    await expect(dial).toBeVisible();

    const up = page.getByRole('button', { name: 'Raise target for Living Room' });
    await up.click();
    await up.click();
    await up.click();
    await expect(dial).toContainText('23.5');
    await page.waitForTimeout(700);
    console.log('ha_service calls:', JSON.stringify(mocks.commands));
    expect(mocks.commands).toHaveLength(1);
    expect(mocks.commands[0]).toMatchObject({
      service: 'set_temperature', entity_id: 'climate.living_room', service_data: { temperature: 23.5 },
    });
    await page.screenshot({ path: shotPath(`${info.project.name}-07-after-bump`) });
  });


  test('widget options menu fits the screen with thumb-sized rows', async ({ page }, info) => {
    await boot(page, { devices: ['climate.living_room'] });
    await page.getByRole('button', { name: 'Widget options' }).click();
    const menu = page.getByRole('menu', { name: /Climate options/ });
    await expect(menu).toBeVisible();

    const m = await menu.evaluate((el) => {
      const b = el.getBoundingClientRect();
      const items = Array.from(el.querySelectorAll<HTMLElement>('[role="menuitem"],[role="menuitemradio"]'))
        .map((n) => ({ t: (n.textContent || '').trim().slice(0, 12), h: Math.round(n.getBoundingClientRect().height) }));
      return { x: b.x, y: b.y, w: b.width, h: b.height, vw: window.innerWidth, vh: window.innerHeight, items };
    });
    const short = m.items.filter((i) => i.h < 43.5);
    console.log(`menu ${Math.round(m.w)}x${Math.round(m.h)} at ${Math.round(m.x)},${Math.round(m.y)} (vp ${m.vw}x${m.vh}); rows under 44px: ${short.map((i) => `${i.t}=${i.h}`).join(', ') || 'none'}`);
    await page.screenshot({ path: shotPath(`${info.project.name}-08-options-menu`) });

    expect(m.x + m.w, 'menu stays inside the screen width').toBeLessThanOrEqual(m.vw + 1);
    expect(m.y + m.h, 'menu clears the bottom edge').toBeLessThanOrEqual(m.vh + 1);
    expect(short, 'menu rows are thumb-sized').toEqual([]);
  });
});
