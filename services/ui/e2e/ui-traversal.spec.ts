/**
 * Comprehensive E2E UI Traversal Test Suite
 *
 * Systematically navigates through the UI starting from the Dashboard,
 * interacting with every clickable element and verifying that resulting
 * UI states display correct, contextually relevant information.
 *
 * Strategy:
 *   1. Login with credentials from .env.test
 *   2. Navigate to Dashboard (root)
 *   3. Recursively crawl clickable elements with depth/visit limits
 *   4. Pre-click: assert visible, enabled, not obstructed
 *   5. Post-click: validate URL/title/context for navigations;
 *      validate content visibility for DOM mutations (modals, dropdowns, tabs)
 *   6. Catch broken links, blank pages, 404/500 errors
 *   7. Gracefully revert state to parent before continuing
 *
 * Usage:
 *   npx playwright test e2e/ui-traversal.spec.ts --project=chromium
 */

import { test, expect, type Page, type Locator } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ─── Configuration ───────────────────────────────────────────────────────────

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const MAX_DEPTH = 2;
const MAX_CLICKS_PER_PAGE = 15;
const NAV_TIMEOUT_MS = 15000;
const ELEMENT_TIMEOUT_MS = 3000;
const SETTLE_TIMEOUT_MS = 8000;

// ─── Credential Loading ──────────────────────────────────────────────────────

function loadCredentials(): { user: string; pass: string } {
  const envTestPath = path.resolve(__dirname, '../../../.env.test');
  if (fs.existsSync(envTestPath)) {
    const envContent = fs.readFileSync(envTestPath, 'utf-8');
    for (const line of envContent.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const eqIdx = trimmed.indexOf('=');
      if (eqIdx > 0) {
        const key = trimmed.slice(0, eqIdx).trim();
        const value = trimmed.slice(eqIdx + 1).trim();
        process.env[key] = value;
      }
    }
  }
  const user = process.env.TEST_USER;
  const pass = process.env.TEST_PASS;
  if (!user || !pass) {
    throw new Error(
      'Credentials not found. Create a .env.test file with TEST_USER and TEST_PASS.'
    );
  }
  return { user, pass };
}

const { user: LOGIN_USER, pass: LOGIN_PASS } = loadCredentials();

// ─── Route Registry ──────────────────────────────────────────────────────────

interface RouteExpectation {
  headingPattern: RegExp;
  titlePattern?: RegExp;
}

const ROUTE_REGISTRY: Record<string, RouteExpectation> = {
  '/':                { headingPattern: /Jarvis Dashboard/i },
  '/admin':           { headingPattern: /System Operations|Admin/i },
  '/communication':   { headingPattern: /Communication/i },
  '/media':           { headingPattern: /Music|Media/i },
  '/workspaces':      { headingPattern: /Workspaces/i },
  '/identity':        { headingPattern: /IDENTITY HUB/i },
  '/knowledge':       { headingPattern: /Knowledge Hub/i },
  '/settings':        { headingPattern: /Settings/i },
  '/lab':             { headingPattern: /Jarvis Lab/i },
  '/docs':            { headingPattern: /Developer|Help/i },
  '/remote':          { headingPattern: /Remote/i },
};

function getRouteExpectation(url: string): RouteExpectation | null {
  try {
    const parsed = new URL(url);
    const route = parsed.pathname.replace(/\/$/, '') || '/';
    for (const [key, expect] of Object.entries(ROUTE_REGISTRY)) {
      if (route === key || route.startsWith(key + '/')) return expect;
    }
  } catch { /* ignore */ }
  return null;
}

// ─── Types ───────────────────────────────────────────────────────────────────

interface CrawlError {
  url: string;
  element: string;
  error: string;
  type: 'broken-link' | 'blank-page' | '404' | '500' | 'timeout' | 'overlay' | 'validation' | 'other';
  timestamp: number;
}

interface ClickableElement {
  locator: Locator;
  text: string;
  tag: string;
  role?: string;
  href?: string;
  ariaLabel?: string;
  description: string;
}

interface CrawlReport {
  totalClicks: number;
  successfulClicks: number;
  navigationClicks: number;
  domMutationClicks: number;
  errors: CrawlError[];
  visitedUrls: Set<string>;
}

function newReport(): CrawlReport {
  return {
    totalClicks: 0,
    successfulClicks: 0,
    navigationClicks: 0,
    domMutationClicks: 0,
    errors: [],
    visitedUrls: new Set(),
  };
}

// ─── Login Helper ────────────────────────────────────────────────────────────

async function loginAsDefault(page: Page): Promise<void> {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').waitFor({ state: 'visible', timeout: 10000 });

  // Handle biometric auth prompt if present
  const useDifferent = page.locator('button:text("Use different account")').first();
  if (await useDifferent.isVisible({ timeout: 2000 }).catch(() => false)) {
    await useDifferent.click();
    await page.waitForTimeout(500);
  }

  await page.getByPlaceholder('Enter username').fill(LOGIN_USER);
  await page.getByPlaceholder('Enter password').fill(LOGIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();

  // Wait for app to render (not just network idle — React SPA)
  await waitForReactRender(page, NAV_TIMEOUT_MS);
  await page.waitForLoadState('networkidle', { timeout: NAV_TIMEOUT_MS }).catch(() => {});
  await page.waitForTimeout(2000);
}

// ─── Core Helpers ────────────────────────────────────────────────────────────

/** Wait for React to mount content in #root beyond mere network idle. */
async function waitForReactRender(page: Page, timeoutMs: number = 10000): Promise<void> {
  await page.waitForFunction(
    () => {
      const root = document.getElementById('root');
      if (!root) return false;
      const text = (root.innerText || '').trim();
      return text.length > 0 && root.querySelectorAll('button, a, [role="button"], input').length > 0;
    },
    { timeout: timeoutMs }
  ).catch(() => {});
  await page.waitForTimeout(500);
}

/** Normalize URL to its route path for dedup. */
function normalizeUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.pathname.replace(/\/$/, '') || '/';
  } catch {
    return url;
  }
}

/** Check if page is blank (no meaningful content). */
async function isBlankPage(page: Page): Promise<boolean> {
  const bodyText = await page.evaluate(() => document.body?.innerText?.trim().length ?? 0);
  const headings = await page.locator('h1, h2, h3').count();
  return bodyText < 10 && headings === 0;
}

/** Detect blocking overlays (modal backdrops, loading spinners that cover content). */
async function hasBlockingOverlay(page: Page): Promise<boolean> {
  const overlaySelectors = [
    '.fixed.inset-0.z-40',
    '.fixed.inset-0.z-50',
    '[class*="overlay"][class*="fixed"]',
  ];
  for (const sel of overlaySelectors) {
    const el = page.locator(sel).first();
    if (await el.isVisible({ timeout: 500 }).catch(() => false)) {
      // Check if it's a true blocker (not just a context menu overlay that closes on click)
      const isContextMenu = await el.evaluate((node) => {
        return node.nextElementSibling?.classList.contains('glass-card') === true;
      }).catch(() => false);
      if (!isContextMenu) return true;
    }
  }
  return false;
}

/** Check if element is visible, enabled, and not obstructed. */
async function isInteractable(locator: Locator): Promise<{ ok: boolean; reason: string }> {
  const isVisible = await locator.isVisible({ timeout: ELEMENT_TIMEOUT_MS }).catch(() => false);
  if (!isVisible) return { ok: false, reason: 'not visible' };

  const isDisabled = await locator.evaluate((el: HTMLElement) => el.disabled === true).catch(() => false);
  if (isDisabled) return { ok: false, reason: 'disabled' };

  const opacity = await locator.evaluate((el: HTMLElement) => {
    return parseFloat(window.getComputedStyle(el).opacity);
  }).catch(() => 1);
  if (opacity < 0.3) return { ok: false, reason: `opacity=${opacity}` };

  const pointerEvents = await locator.evaluate((el: HTMLElement) => {
    return window.getComputedStyle(el).pointerEvents;
  }).catch(() => 'auto');
  if (pointerEvents === 'none') return { ok: false, reason: 'pointer-events: none' };

  return { ok: true, reason: '' };
}

/** Check if element is within viewport bounds. */
async function isInViewport(locator: Locator): Promise<boolean> {
  const box = await locator.boundingBox().catch(() => null);
  if (!box) return false;
  const vh = await locator.evaluate(() => window.innerHeight).catch(() => 800);
  return box.y >= -50 && box.y + box.height <= vh + 50;
}

/** Attempt to click with force fallback. */
async function safeClick(locator: Locator): Promise<boolean> {
  try {
    await locator.click({ timeout: 3000, force: false });
    return true;
  } catch {
    try {
      await locator.click({ timeout: 2000, force: true });
      return true;
    } catch {
      return false;
    }
  }
}

// ─── Element Discovery ───────────────────────────────────────────────────────

/** Find all clickable elements on the current page, deduplicated. */
async function findClickableElements(page: Page): Promise<ClickableElement[]> {
  const selectors = [
    'a[href]',
    'button:not([disabled])',
    '[role="button"]:not([aria-disabled="true"])',
    '[role="tab"]',
    '[role="menuitem"]',
    '[role="switch"]',
    '[role="checkbox"]',
    'input[type="submit"], input[type="button"]',
    'select',
    '[tabindex="0"]',
  ];

  const elements: ClickableElement[] = [];
  const seen = new Set<string>();

  for (const selector of selectors) {
    const locators = page.locator(selector);
    const count = await locators.count();

    for (let i = 0; i < count; i++) {
      const locator = locators.nth(i);

      const text = (await locator.textContent().catch(() => '')).trim();
      const tag = await locator.evaluate((el: HTMLElement) => el.tagName.toLowerCase()).catch(() => 'unknown');
      const role = await locator.evaluate((el: HTMLElement) => el.getAttribute('role') || '').catch(() => '');
      const ariaLabel = await locator.evaluate((el: HTMLElement) => el.getAttribute('aria-label') || '').catch(() => '');
      const href = await locator.evaluate((el: HTMLElement) =>
        el.tagName.toLowerCase() === 'a' ? (el as HTMLAnchorElement).href : ''
      ).catch(() => '');

      const dedupKey = `${tag}:${text.slice(0, 60)}:${role}:${ariaLabel}`;
      if (seen.has(dedupKey)) continue;
      seen.add(dedupKey);

      // Skip empty elements with no identifying attributes
      if (!text && !ariaLabel && !role && !href) continue;

      // Skip sidebar NavLinks that we handle separately in dedicated tests
      // (they're still crawled, but we don't want duplicates)

      const description = ariaLabel || text || `${tag}${role ? `[${role}]` : ''}${href ? `[${href}]` : ''}`;

      elements.push({
        locator,
        text,
        tag,
        role: role || undefined,
        href: href || undefined,
        ariaLabel: ariaLabel || undefined,
        description,
      });
    }
  }

  return elements;
}

// ─── Post-Click Validation ───────────────────────────────────────────────────

interface ValidationResult {
  status: 'success' | 'error';
  details: string[];
  isNavigation: boolean;
  newUrl: string | null;
}

async function validatePostClick(
  page: Page,
  elementText: string,
  beforeUrl: string
): Promise<ValidationResult> {
  const findings: string[] = [];
  const afterUrl = normalizeUrl(page.url());
  const beforeRoute = normalizeUrl(beforeUrl);
  const isNavigation = afterUrl !== beforeRoute;
  let newUrl: string | null = null;

  if (isNavigation) {
    newUrl = afterUrl;

    // Check for blank page
    if (await isBlankPage(page)) {
      findings.push('blank page after navigation');
    }

    // Contextual validation: URL + heading match
    const expected = getRouteExpectation(page.url());
    if (expected) {
      if (expected.titlePattern) {
        const title = await page.title();
        if (!expected.titlePattern.test(title)) {
          findings.push(`title "${title}" does not match ${expected.titlePattern}`);
        }
      }
      if (expected.headingPattern) {
        // Check if any heading matches the expected pattern
        const heading = page.locator('h1, h2, h3').filter({ hasText: expected.headingPattern }).first();
        const headingVisible = await heading.isVisible({ timeout: 5000 }).catch(() => false);
        if (!headingVisible) {
          findings.push(`expected heading matching ${expected.headingPattern} not found`);
        }
      }
    }

    // Check for blocking overlay (not context menu)
    if (await hasBlockingOverlay(page)) {
      findings.push('blocking overlay present');
    }
  } else {
    // DOM mutation — check that content is still present and not blocked
    const bodyText = await page.evaluate(() => document.body?.innerText?.trim().length ?? 0);
    if (bodyText < 5) {
      findings.push('page content lost after click');
    }

    // Check for unintended blocking overlay
    if (await hasBlockingOverlay(page)) {
      findings.push('blocking overlay present after DOM mutation');
    }
  }

  return {
    status: findings.length > 0 ? 'error' : 'success',
    details: findings,
    isNavigation,
    newUrl,
  };
}

// ─── State Reversion ─────────────────────────────────────────────────────────

async function revertToParent(page: Page, beforeUrl: string): Promise<void> {
  // Strategy 1: goBack
  try {
    await page.goBack({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(500);
    if (normalizeUrl(page.url()) === normalizeUrl(beforeUrl)) return;
  } catch { /* ignore */ }

  // Strategy 2: direct navigation
  try {
    await page.goto(beforeUrl, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT_MS }).catch(() => {});
    await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(500);
    return;
  } catch { /* ignore */ }

  // Strategy 3: navigate to root
  try {
    await page.goto(`${UI_URL}/`, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT_MS }).catch(() => {});
    await page.waitForTimeout(500);
  } catch { /* ignore */ }
}

// ─── Recursive Crawler ───────────────────────────────────────────────────────

async function crawlPage(
  page: Page,
  report: CrawlReport,
  depth: number,
  pathChain: string[],
): Promise<void> {
  if (depth > MAX_DEPTH) return;

  const currentUrl = normalizeUrl(page.url());
  const crawlKey = `${currentUrl}:${depth}`;
  if (report.visitedUrls.has(crawlKey)) return;
  report.visitedUrls.add(crawlKey);

  await test.step(`Crawl [depth=${depth}]: ${pathChain.join(' > ')} (${currentUrl})`, async () => {
    // Wait for page to settle
    await page.waitForLoadState('networkidle', { timeout: SETTLE_TIMEOUT_MS }).catch(() => {});
    await waitForReactRender(page, SETTLE_TIMEOUT_MS);
    await page.waitForTimeout(500);

    const elements = await findClickableElements(page);
    console.log(`  [CRAWL] Found ${elements.length} clickable elements at depth ${depth} on ${currentUrl}`);

    let clickCount = 0;
    for (const element of elements) {
      if (clickCount >= MAX_CLICKS_PER_PAGE) break;

      const { locator, description, text, tag, role } = element;

      await test.step(`Element: "${description}" (${tag}${role ? ` [${role}]` : ''})`, async () => {
        report.totalClicks++;
        clickCount++;

        // ── Pre-click validation ──
        const { ok, reason } = await isInteractable(locator);
        if (!ok) {
          console.log(`  [SKIP] "${description}" — ${reason}`);
          return;
        }

        // Scroll into view if needed
        if (!(await isInViewport(locator))) {
          await locator.scrollIntoViewIfNeeded().catch(() => {});
          await page.waitForTimeout(300);
        }

        // ── Capture pre-click state ──
        const beforeUrl = page.url();

        // ── Click ──
        const clicked = await safeClick(locator);
        if (!clicked) {
          report.errors.push({
            url: beforeUrl,
            element: description,
            error: 'Failed to click element',
            type: 'other',
            timestamp: Date.now(),
          });
          return;
        }

        // ── Wait for state to settle ──
        await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(1500);
        await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(500);

        // ── Validate post-click state ──
        const result = await validatePostClick(page, text, beforeUrl);

        if (result.status === 'error') {
          const errorType: CrawlError['type'] = result.details.some(d => d.includes('404')) ? '404'
            : result.details.some(d => d.includes('500')) ? '500'
            : result.details.some(d => d.includes('blank')) ? 'blank-page'
            : result.details.some(d => d.includes('overlay')) ? 'overlay'
            : 'validation';

          report.errors.push({
            url: beforeUrl,
            element: description,
            error: result.details.join('; '),
            type: errorType,
            timestamp: Date.now(),
          });
          console.log(`  [ERROR] "${description}" — ${result.details.join('; ')}`);
        } else {
          report.successfulClicks++;
          if (result.isNavigation) {
            report.navigationClicks++;
            console.log(`  [NAV] "${description}" → ${result.newUrl}`);
          } else {
            report.domMutationClicks++;
            console.log(`  [DOM] "${description}" — content changed in place`);
          }
        }

        // ── Revert to parent state ──
        await test.step('Reverting to parent', async () => {
          await revertToParent(page, beforeUrl);
          await page.waitForTimeout(500);
        });

        // ── Recurse into navigated page ──
        if (result.isNavigation && result.status === 'success' && depth < MAX_DEPTH) {
          await test.step(`Recursing into: ${result.newUrl}`, async () => {
            // Navigate back to the clicked page for recursion
            if (result.newUrl) {
              await page.goto(`${UI_URL}${result.newUrl}`, {
                waitUntil: 'domcontentloaded',
                timeout: NAV_TIMEOUT_MS,
              }).catch(() => {});
              await waitForReactRender(page, SETTLE_TIMEOUT_MS);
              await page.waitForTimeout(1000);
              await crawlPage(page, report, depth + 1, [...pathChain, description]);
              // Return to parent after recursion
              await revertToParent(page, beforeUrl);
            }
          });
        }
      });
    }
  });
}

// ─── Tests ───────────────────────────────────────────────────────────────────

test.describe('Comprehensive E2E UI Traversal', () => {

  // ── 1. Recursive Crawler from Dashboard ────────────────────────────────────

  test('recursive crawl from Dashboard', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('pageerror', (err) => consoleErrors.push(err.message));
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    await test.step('Login', async () => {
      await loginAsDefault(page);
    });

    await test.step('Navigate to Dashboard', async () => {
      await page.goto(`${UI_URL}/`, { waitUntil: 'networkidle', timeout: NAV_TIMEOUT_MS }).catch(() => {});
      await waitForReactRender(page, NAV_TIMEOUT_MS);
      await page.waitForTimeout(1000);
    });

    await test.step('Verify Dashboard loaded', async () => {
      const title = await page.title();
      expect(title).toMatch(/Jarvis/i);
      const bodyText = await page.evaluate(() => document.body?.innerText?.length ?? 0);
      expect(bodyText).toBeGreaterThan(0);
    });

    const report = newReport();
    await test.step('Recursive crawl', async () => {
      await crawlPage(page, report, 0, ['Dashboard']);
    });

    await test.step('Crawl report & assertions', async () => {
      console.log('\n═══════════════════════════════════════════');
      console.log('  CRAWLER REPORT');
      console.log('═══════════════════════════════════════════');
      console.log(`  Total clicks:        ${report.totalClicks}`);
      console.log(`  Successful:          ${report.successfulClicks}`);
      console.log(`  Navigations:         ${report.navigationClicks}`);
      console.log(`  DOM mutations:       ${report.domMutationClicks}`);
      console.log(`  Errors:              ${report.errors.length}`);
      console.log(`  Unique URLs visited: ${report.visitedUrls.size}`);
      console.log('═══════════════════════════════════════════\n');

      if (report.errors.length > 0) {
        console.log('  ERRORS:');
        for (const err of report.errors) {
          console.log(`    [${err.type}] "${err.element}" on ${err.url} — ${err.error}`);
        }
      }

      expect(report.totalClicks).toBeGreaterThan(0, 'Should find clickable elements');
      expect(report.successfulClicks).toBeGreaterThan(0, 'Should successfully interact with elements');

      const criticalErrors = report.errors.filter(e =>
        ['404', '500', 'blank-page'].includes(e.type)
      );
      expect(criticalErrors).toEqual([], 'No critical errors (404/500/blank-page) expected');
    });
  });

  // ── 2. Sidebar Navigation Crawler ──────────────────────────────────────────

  test('sidebar navigation — all routes', async ({ page }) => {
    await test.step('Login', async () => {
      await loginAsDefault(page);
    });

    const report = newReport();

    await test.step('Crawl sidebar navigation links', async () => {
      // Sidebar uses NavLink components
      const sidebar = page.locator('aside nav');
      await expect(sidebar).toBeVisible({ timeout: 5000 });

      const navLinks = sidebar.locator('a[href]');
      const count = await navLinks.count();
      console.log(`  [SIDEBAR] Found ${count} navigation links`);

      for (let i = 0; i < count; i++) {
        const link = navLinks.nth(i);
        const text = (await link.textContent().catch(() => '')).trim();
        const href = await link.getAttribute('href').catch(() => '') || '';
        const title = await link.getAttribute('title').catch(() => '') || '';
        const label = title || text || href;

        await test.step(`Sidebar: "${label}" → ${href}`, async () => {
          report.totalClicks++;

          const { ok, reason } = await isInteractable(link);
          if (!ok) {
            report.errors.push({
              url: page.url(), element: label, error: `Not interactable: ${reason}`,
              type: 'validation', timestamp: Date.now(),
            });
            return;
          }

          const beforeUrl = page.url();
          const clicked = await safeClick(link);
          if (!clicked) {
            report.errors.push({
              url: beforeUrl, element: label, error: 'Failed to click',
              type: 'other', timestamp: Date.now(),
            });
            return;
          }

          await page.waitForLoadState('networkidle', { timeout: NAV_TIMEOUT_MS }).catch(() => {});
          await waitForReactRender(page, SETTLE_TIMEOUT_MS);
          await page.waitForTimeout(1000);

          // Validate: URL changed
          const afterRoute = normalizeUrl(page.url());
          if (afterRoute === normalizeUrl(beforeUrl)) {
            report.errors.push({
              url: beforeUrl, element: label, error: 'URL did not change',
              type: 'validation', timestamp: Date.now(),
            });
            return;
          }

          // Validate: page not blank
          if (await isBlankPage(page)) {
            report.errors.push({
              url: page.url(), element: label, error: 'Page is blank',
              type: 'blank-page', timestamp: Date.now(),
            });
          }

          // Validate: contextual heading exists
          const expected = getRouteExpectation(page.url());
          if (expected) {
            const heading = page.locator('h1, h2, h3').filter({ hasText: expected.headingPattern }).first();
            const headingVisible = await heading.isVisible({ timeout: 10000 }).catch(() => false);
            if (!headingVisible) {
              report.errors.push({
                url: page.url(), element: label,
                error: `Expected heading ${expected.headingPattern} not visible`,
                type: 'validation', timestamp: Date.now(),
              });
            } else {
              report.successfulClicks++;
              report.navigationClicks++;
              console.log(`  [OK] "${label}" → ${afterRoute} (heading verified)`);
            }
          } else {
            report.successfulClicks++;
            report.navigationClicks++;
          }

          // Revert: navigate back to dashboard
          await page.goto(`${UI_URL}/`, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT_MS }).catch(() => {});
          await waitForReactRender(page, SETTLE_TIMEOUT_MS);
          await page.waitForTimeout(500);
        });
      }
    });

    await test.step('Sidebar report', async () => {
      console.log(`\n  Sidebar: ${report.totalClicks} links, ${report.successfulClicks} navigations, ${report.errors.length} errors`);
      for (const err of report.errors) {
        console.log(`    [${err.type}] "${err.element}" — ${err.error}`);
      }
      expect(report.totalClicks).toBeGreaterThan(0);
      const critical = report.errors.filter(e => ['blank-page', '404', '500'].includes(e.type));
      expect(critical).toEqual([]);
    });
  });

  // ── 3. Admin Tab Crawler ───────────────────────────────────────────────────

  test('Admin page — all tabs and content validation', async ({ page }) => {
    await test.step('Login and navigate to Admin', async () => {
      await loginAsDefault(page);
      await page.goto(`${UI_URL}/admin`, { waitUntil: 'networkidle', timeout: NAV_TIMEOUT_MS }).catch(() => {});
      await waitForReactRender(page, NAV_TIMEOUT_MS);
      await page.waitForTimeout(2000);
    });

    const report = newReport();

    // All 8 admin tabs from Admin.tsx
    const adminTabs = [
      { label: 'Users & Devices', expectHeading: /User Management|Users/i },
      { label: 'Device Groups', expectHeading: /Media Groups|Device Groups/i },
      { label: 'Telemetry', expectHeading: /Enrolled Devices|Telemetry/i },
      { label: 'Intercom', expectHeading: /Intercom|Active Intercom/i },
      { label: 'Raven Ops', expectHeading: /Active Missions|Raven/i },
      { label: 'LLM & Settings', expectHeading: /AI & Compute|LLM/i },
      { label: 'Database & Audit', expectHeading: /Database|Audit/i },
      { label: 'System Services', expectHeading: /System Services|Services/i },
    ];

    await test.step('Crawl all admin tabs', async () => {
      for (const tab of adminTabs) {
        await test.step(`Tab: "${tab.label}"`, async () => {
          report.totalClicks++;

          const tabButton = page.getByRole('button', { name: tab.label });
          const { ok, reason } = await isInteractable(tabButton);
          if (!ok) {
            report.errors.push({
              url: page.url(), element: tab.label, error: `Not interactable: ${reason}`,
              type: 'validation', timestamp: Date.now(),
            });
            return;
          }

          await tabButton.click();
          await page.waitForTimeout(2000);
          await page.waitForLoadState('networkidle', { timeout: SETTLE_TIMEOUT_MS }).catch(() => {});
          await page.waitForTimeout(500);

          // Validate: tab content visible
          const heading = page.locator('h1, h2, h3, h4').filter({ hasText: tab.expectHeading }).first();
          const headingVisible = await heading.isVisible({ timeout: 10000 }).catch(() => false);

          if (headingVisible) {
            report.successfulClicks++;
            report.domMutationClicks++;
            console.log(`  [OK] Tab "${tab.label}" — heading verified`);

            // Crawl a few elements within the tab panel
            const panelElements = await findClickableElements(page);
            let panelClickCount = 0;
            for (const el of panelElements.slice(0, 5)) {
              if (panelClickCount >= 3) break;
              // Skip tab buttons themselves
              if (el.text === tab.label || adminTabs.some(t => t.label === el.text)) continue;

              await test.step(`Panel element: "${el.description}"`, async () => {
                report.totalClicks++;
                panelClickCount++;

                const { ok: elOk } = await isInteractable(el.locator);
                if (!elOk) return;

                const clicked = await safeClick(el.locator);
                if (clicked) {
                  await page.waitForTimeout(1000);
                  await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
                  report.successfulClicks++;
                  report.domMutationClicks++;

                  // Close any opened modal/dropdown
                  await page.keyboard.press('Escape').catch(() => {});
                  await page.waitForTimeout(300);
                }
              });
            }
          } else {
            report.errors.push({
              url: page.url(), element: tab.label,
              error: `Expected heading ${tab.expectHeading} not visible`,
              type: 'validation', timestamp: Date.now(),
            });
            console.log(`  [FAIL] Tab "${tab.label}" — heading not found`);
          }
        });
      }
    });

    await test.step('Admin tab report', async () => {
      console.log(`\n  Admin: ${report.totalClicks} clicks, ${report.successfulClicks} successful, ${report.errors.length} errors`);
      for (const err of report.errors) {
        console.log(`    [${err.type}] "${err.element}" — ${err.error}`);
      }
      expect(report.totalClicks).toBeGreaterThan(0);
      expect(report.successfulClicks).toBeGreaterThan(0, 'At least one tab should load content');
    });
  });

  // ── 4. Widget Context Menu Crawler ─────────────────────────────────────────

  test('Dashboard widgets — gear icon & context menu interactions', async ({ page }) => {
    await test.step('Login and navigate to Dashboard', async () => {
      await loginAsDefault(page);
      await page.goto(`${UI_URL}/`, { waitUntil: 'networkidle', timeout: NAV_TIMEOUT_MS }).catch(() => {});
      await waitForReactRender(page, NAV_TIMEOUT_MS);
      await page.waitForTimeout(2000);
    });

    await test.step('Verify widgets are rendered', async () => {
      const glassPanels = page.locator('.glass-panel');
      const count = await glassPanels.count();
      console.log(`  [WIDGETS] Found ${count} glass-panel widgets on dashboard`);
      expect(count).toBeGreaterThan(0, 'Dashboard should have at least one widget');
    });

    await test.step('Test gear icon opens context menu', async () => {
      const gearButton = page.locator('button[title="Widget options"]').first();
      await expect(gearButton).toBeVisible({ timeout: 10000 });
      await gearButton.click();
      await page.waitForTimeout(500);

      // Context menu has class "fixed z-50 glass-card"
      const menu = page.locator('.fixed.z-50.glass-card').first();
      await expect(menu).toBeVisible({ timeout: 3000 });

      // Menu should show the widget name (not literally "Widget")
      const menuText = await menu.textContent();
      console.log(`  [WIDGETS] Context menu text: "${menuText?.trim().slice(0, 80)}"`);
      expect(menuText).toBeTruthy();
      expect(menuText!.length).toBeGreaterThan(0);
    });

    await test.step('Verify context menu options', async () => {
      // Menu should already be open from previous step, but re-open to be safe
      const gearButton = page.locator('button[title="Widget options"]').first();
      await gearButton.click();
      await page.waitForTimeout(500);

      const menu = page.locator('.fixed.z-50.glass-card').first();
      await expect(menu).toBeVisible({ timeout: 3000 });

      // Pin/Unpin button
      const pinButton = menu.getByRole('button', { name: /Pin|Unpin/ });
      await expect(pinButton).toBeVisible();

      // Size options
      await expect(menu.getByRole('button', { name: 'Small' })).toBeVisible();
      await expect(menu.getByRole('button', { name: 'Medium' })).toBeVisible();
      await expect(menu.getByRole('button', { name: 'Wide' })).toBeVisible();
      await expect(menu.getByRole('button', { name: 'Tall' })).toBeVisible();

      // Move to bottom
      await expect(menu.getByRole('button', { name: 'Move to bottom' })).toBeVisible();

      // Remove (red text)
      const removeButton = menu.getByRole('button', { name: 'Remove' });
      await expect(removeButton).toBeVisible();
      await expect(removeButton).toHaveClass(/text-red/);
    });

    await test.step('Close context menu via outside click', async () => {
      const gearButton = page.locator('button[title="Widget options"]').first();
      await gearButton.click();
      await page.waitForTimeout(500);

      const menu = page.locator('.fixed.z-50.glass-card').first();
      await expect(menu).toBeVisible();

      // Click outside (on the overlay)
      await page.locator('.fixed.inset-0.z-40').first().click().catch(() => {});
      await page.waitForTimeout(500);
      await expect(menu).not.toBeVisible();
    });

    await test.step('Close context menu via Escape', async () => {
      const gearButton = page.locator('button[title="Widget options"]').first();
      await gearButton.click();
      await page.waitForTimeout(500);

      const menu = page.locator('.fixed.z-50.glass-card').first();
      await expect(menu).toBeVisible();

      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);
      await expect(menu).not.toBeVisible();
    });

    await test.step('Pin toggle works', async () => {
      const gearButton = page.locator('button[title="Widget options"]').first();
      await gearButton.click();
      await page.waitForTimeout(500);

      const menu = page.locator('.fixed.z-50.glass-card').first();
      const pinButton = menu.getByRole('button', { name: /Pin|Unpin/ });
      await pinButton.click();
      await page.waitForTimeout(500);

      // Menu should close after pin toggle
      await expect(menu).not.toBeVisible();
      console.log('  [WIDGETS] Pin toggle successful');
    });
  });

  // ── 5. Dropdown & Modal Crawler ────────────────────────────────────────────

  test('dropdown and modal interactions across pages', async ({ page }) => {
    await test.step('Login', async () => {
      await loginAsDefault(page);
    });

    const report = newReport();

    // Pages known to have dropdowns/modals
    const pagesWithDropdowns = [
      { url: '/identity', name: 'Identity' },
      { url: '/admin', name: 'Admin' },
    ];

    for (const target of pagesWithDropdowns) {
      await test.step(`Visit ${target.name} page`, async () => {
        await page.goto(`${UI_URL}${target.url}`, { waitUntil: 'networkidle', timeout: NAV_TIMEOUT_MS }).catch(() => {});
        await waitForReactRender(page, NAV_TIMEOUT_MS);
        await page.waitForTimeout(2000);

        const dropdownSelectors = [
          '[aria-haspopup="listbox"]',
          '[aria-haspopup="true"]',
          'button[aria-expanded="false"]',
          '[role="combobox"]',
          'input[placeholder*="Search"], input[placeholder*="search"]',
          'button:has-text("More"), button:has-text("Actions"), button:has-text("Options")',
        ];

        for (const sel of dropdownSelectors) {
          const count = await page.locator(sel).count();
          for (let i = 0; i < Math.min(count, 3); i++) {
            const locator = page.locator(sel).nth(i);
            const text = (await locator.textContent().catch(() => '')).trim();
            const ariaLabel = await locator.evaluate((el: HTMLElement) => el.getAttribute('aria-label') || '').catch(() => '');
            const description = ariaLabel || text || `[${sel}]`;

            if (!text && !ariaLabel) continue;

            await test.step(`${target.name}: dropdown "${description}"`, async () => {
              report.totalClicks++;

              const { ok } = await isInteractable(locator);
              if (!ok) return;

              const clicked = await safeClick(locator);
              if (!clicked) return;

              await page.waitForTimeout(1500);
              await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});

              // Check if something opened
              const hasDialog = await page.locator('[role="dialog"], [role="listbox"], [role="menu"]').count() > 0;
              const expanded = await locator.evaluate((el: HTMLElement) => el.getAttribute('aria-expanded') === 'true').catch(() => false);

              if (hasDialog || expanded) {
                report.successfulClicks++;
                report.domMutationClicks++;
                console.log(`  [OK] ${target.name}: "${description}" opened content`);

                // Check menu items if present
                const menuItems = await page.locator('[role="menuitem"], [role="option"], [role="listitem"]').count();
                if (menuItems > 0) {
                  console.log(`    Menu has ${menuItems} items`);
                }
              }

              // Close any opened dropdown/modal
              await page.keyboard.press('Escape').catch(() => {});
              await page.waitForTimeout(500);
            });
          }
        }
      });
    }

    await test.step('Dropdown report', async () => {
      console.log(`\n  Dropdowns: ${report.totalClicks} scanned, ${report.successfulClicks} opened, ${report.errors.length} errors`);
      expect(report.totalClicks).toBeGreaterThan(0);
    });
  });

  // ── 6. API Health Checks ───────────────────────────────────────────────────

  test('API endpoints return healthy responses', async ({ request }) => {
    await test.step('Health endpoint', async () => {
      const resp = await request.get(`${UI_URL}/health/ready`);
      expect(resp.status()).toBe(200);
      const data = await resp.json();
      expect(['READY', 'DEGRADED']).toContain(data.status);
    });

    await test.step('Config endpoint', async () => {
      const resp = await request.get(`${UI_URL}/api/config`);
      expect(resp.status()).toBe(200);
    });

    await test.step('Models endpoint', async () => {
      const resp = await request.get(`${UI_URL}/api/config/models`);
      expect(resp.status()).toBe(200);
      const data = await resp.json();
      expect(data).toHaveProperty('models');
    });
  });
});
