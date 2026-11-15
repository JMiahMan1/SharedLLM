import type { Locator, Page, TestInfo } from '@playwright/test';
import { controlsOffScreen } from './layoutChecks';
import { audit, isPhone } from './screenAudit';

/**
 * One screen of a page (the page itself, or a dialog, sheet or editor opened
 * on it), checked the way the family's phones see it: every folded section
 * opened, then the shared audit (44px targets on phones, no hard-clipped text,
 * nothing drawn past the card or dialog it sits in), no control past the edge
 * of the screen and no sideways page scroll. Findings are returned rather than
 * asserted, so one run reports every problem on every screen.
 */

/** Open every folded <details> under `scope` (clicking the summary, as a person would). */
export async function openAllDetails(scope: Locator): Promise<void> {
  for (let pass = 0; pass < 3; pass += 1) {
    const closed = await scope.locator('details:not([open]) > summary').all();
    if (!closed.length) return;
    for (const summary of closed) {
      if (await summary.isVisible().catch(() => false)) await summary.click({ timeout: 2000 }).catch(() => {});
    }
    await scope.page().waitForTimeout(150);
  }
}

/**
 * A finding the audit reports for a deliberate design, by screen and text. Each
 * entry says why; nothing goes here to make a real bug pass.
 */
export interface Allowed {
  where: RegExp;
  finding: RegExp;
  why: string;
}

/** Every problem on one screen, each prefixed with `where`. */
export async function screenFindings(page: Page, scope: Locator, where: string, allowed: Allowed[] = []): Promise<string[]> {
  const f = await audit(scope, isPhone(page));
  const out = [
    ...f.small.map((s) => `${where}: under 44px: ${s}`),
    ...f.clipped.map((s) => `${where}: clipped: ${s}`),
    ...f.escaped.map((s) => `${where}: outside its card: ${s}`),
    ...(await controlsOffScreen(page)).map((s) => `${where}: off the screen: ${s}`),
  ];
  const o = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (o > 1) out.push(`${where}: the page scrolls sideways by ${o}px`);
  return out.filter((line) => !allowed.some((a) => a.where.test(where) && a.finding.test(line)));
}

/** Wait for a dialog or sheet to stop moving (its slide-in or fade has finished). */
export async function settle(page: Page): Promise<void> {
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running'), null, { timeout: 3000 })
    .catch(() => {});
  await page.waitForTimeout(150);
}

/** A screenshot for the report (and for looking at by eye). */
export async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  const path = info.outputPath(`${name}-${info.project.name}.png`);
  await page.screenshot({ path });
  await info.attach(`${name} (${info.project.name})`, { path, contentType: 'image/png' });
}

/**
 * Answer the dev server's hot-reload socket and say nothing on it. A file saved
 * while a run is under way (another checkout's work, an editor) otherwise
 * reloads the page under a test, and an open dialog vanishes mid-check.
 */
export async function quietHotReload(page: Page): Promise<void> {
  await page.routeWebSocket((url) => url.pathname === '/' && url.searchParams.has('token'), () => {});
}
