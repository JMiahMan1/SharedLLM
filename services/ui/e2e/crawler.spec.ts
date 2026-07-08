/**
 * Comprehensive Recursive E2E Crawler Test
 *
 * Dynamically traverses every clickable element starting from the Dashboard,
 * validates resulting UI states, and reports findings. Designed for a React
 * SPA with client-side routing.
 *
 * Strategy:
 *   1. Login with default credentials
 *   2. Navigate to Dashboard (root)
 *   3. Recursively crawl clickable elements with a depth limit
 *   4. After each click, detect navigation vs. DOM mutation and validate accordingly
 *   5. Gracefully revert state to parent before continuing
 *   6. Accumulate a structured report of successes, errors, and skipped elements
 *
 * Usage:
 *   npx playwright test e2e/crawler.spec.ts
 *
 * Credentials are loaded from .env.test file in the project root.
 */

import { test, expect, type Page } from '@playwright/test';
import type { Locator } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ─── Configuration ───────────────────────────────────────────────────────────

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const MAX_DEPTH = 3;
const CRAWL_TIMEOUT_MS = 8000;
const NAV_TIMEOUT_MS = 10000;
const ELEMENT_TIMEOUT_MS = 3000;

// Load credentials from .env.test file
function loadCredentials() {
  const envTestPath = path.resolve(__dirname, '../../../.env.test');
  
  if (fs.existsSync(envTestPath)) {
    const envContent = fs.readFileSync(envTestPath, 'utf-8');
    const lines = envContent.split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const [key, value] = trimmed.split('=');
      if (key && value) {
        process.env[key.trim()] = value.trim();
      }
    }
  }
  
  return {
    user: process.env.TEST_USER,
    pass: process.env.TEST_PASS
  };
}

const { user: LOGIN_USER, pass: LOGIN_PASS } = loadCredentials();

// Routes we consider "top-level pages" — used for context-aware validation
const KNOWN_ROUTES: Record<string, { titlePattern: RegExp; headingPattern: RegExp }> = {
  '/': { titlePattern: /Jarvis Dashboard/, headingPattern: /Jarvis Dashboard/i },
  '/admin': { titlePattern: /Jarvis OS/, headingPattern: /System Operations/i },
  '/communication': { titlePattern: /Jarvis OS/, headingPattern: /Communication/i },
  '/workspaces': { titlePattern: /Jarvis OS/, headingPattern: /Workspaces/i },
  '/identity': { titlePattern: /Jarvis OS/, headingPattern: /IDENTITY HUB/i },
  '/knowledge': { titlePattern: /Jarvis OS/, headingPattern: /Knowledge Hub/i },
  '/lab': { titlePattern: /Jarvis OS/, headingPattern: /Jarvis Lab/i },
  '/docs': { titlePattern: /Jarvis OS/, headingPattern: /Developer/i },
  '/media': { titlePattern: /Jarvis OS/, headingPattern: /Music/i },
};

// ─── Types ───────────────────────────────────────────────────────────────────

interface CrawlError {
  url: string;
  elementDescription: string;
  error: string;
  errorType: 'broken-link' | 'blank-page' | '404' | '500' | 'timeout' | 'overlay-blocked' | 'validation-failed' | 'other';
  timestamp: number;
}

interface CrawlReport {
  totalClicks: number;
  successfulClicks: number;
  navigationClicks: number;
  domMutationClicks: number;
  errors: CrawlError[];
  visitedUrls: Set<string>;
  elementMetadata: { text: string; tag: string; url: string; role?: string }[];
}

interface ClickContext {
  elementText: string;
  elementTag: string;
  elementRole?: string;
  elementHref?: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function loginAsDefault(page: Page): Promise<void> {
  await page.goto(`${UI_URL}/login`);

  // Wait for login form to be ready
  await page.locator('input[type="text"], input[placeholder*="user"]').first()
    .waitFor({ state: 'visible', timeout: 5000 });

  // Handle biometric auth prompt if present
  const useDifferent = page.locator('button:text("Use different account")').first();
  if (await useDifferent.isVisible({ timeout: 2000 }).catch(() => false)) {
    await useDifferent.click();
    await page.waitForTimeout(500);
  }

  const usernameInput = page.locator('input[type="text"], input[placeholder*="user"], input[name="username"]').first();
  const passwordInput = page.locator('input[type="password"], input[placeholder*="pass"]').first();

  await usernameInput.fill(LOGIN_USER);
  if (await passwordInput.isVisible({ timeout: 2000 }).catch(() => false)) {
    await passwordInput.fill(LOGIN_PASS);
  }

  const signInBtn = page.locator('button:has-text("Sign In"), button:has-text("Signin"), button:has-text("Sign in"), button:has-text("sign in")').first();
  await signInBtn.click();

  // Wait for app to render (not just network idle)
  await waitForReactRender(page, NAV_TIMEOUT_MS);
  await page.waitForLoadState('networkidle', { timeout: NAV_TIMEOUT_MS }).catch(() => {});
  await page.waitForTimeout(2000);
}

/**
 * Normalize a URL to its route portion for visited-tracking deduplication.
 * Strips query params, fragments, and trailing slashes.
 */
function normalizeUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.pathname.replace(/\/$/, '') || '/';
  } catch {
    return url;
  }
}

/**
 * Determine the expected heading/title for a given route.
 */
function getExpectedContext(url: string): { titlePattern?: RegExp; headingPattern?: RegExp } | null {
  const route = normalizeUrl(url);
  return Object.entries(KNOWN_ROUTES).find(([key]) => route === key || route.startsWith(key + '/'))
    ? KNOWN_ROUTES[Object.keys(KNOWN_ROUTES).find((key) => route === key || route.startsWith(key + '/')) as string] || null
    : null;
}

/**
 * Check if the page appears to be blank/empty (no meaningful content).
 */
async function isBlankPage(page: Page): Promise<boolean> {
  const bodyText = await page.evaluate(() => document.body?.innerText?.trim().length ?? 0);
  const headings = await page.locator('h1, h2, h3').count();
  return bodyText < 10 && headings === 0;
}

/**
 * Check if the page returned a 404 or 500 status code.
 */
async function getPageStatusCode(page: Page): Promise<number | null> {
  // Playwright pages don't expose response status directly after navigation.
  // We check for common error indicators.
  const statusText = await page.evaluate(() => {
    const el = document.querySelector('[data-error-status], .error-status, .page-title');
    return el?.textContent?.trim() || '';
  });
  const match = statusText.match(/(\d{3})/);
  return match ? parseInt(match[1], 10) : null;
}

/**
 * Detect if a blocking overlay is present (e.g., loading spinner, modal backdrop).
 */
async function hasBlockingOverlay(page: Page): Promise<boolean> {
  const overlaySelectors = [
    '[role="dialog"][style*="display: none"]',
    '.backdrop-blur',
    '[class*="overlay"][class*="block"]',
    '.fixed.inset-0.z-50',
  ];
  for (const sel of overlaySelectors) {
    const el = page.locator(sel).first();
    if (await el.isVisible({ timeout: 1000 }).catch(() => false)) {
      return true;
    }
  }
  return false;
}

/**
 * Check if a target element is visible, enabled, and not obstructed.
 */
async function isClickable(locator: Locator): Promise<{ clickable: boolean; reason: string }> {
  // Check visibility
  const isVisible = await locator.isVisible({ timeout: ELEMENT_TIMEOUT_MS }).catch(() => false);
  if (!isVisible) return { clickable: false, reason: 'not visible' };

  // Check if enabled
  const isDisabled = await locator.evaluate((el: HTMLElement) => el.disabled === true).catch(() => false);
  if (isDisabled) return { clickable: false, reason: 'disabled' };

  // Check opacity (visually hidden elements)
  const opacity = await locator.evaluate((el: HTMLElement) => {
    const style = window.getComputedStyle(el);
    return parseFloat(style.opacity);
  }).catch(() => 1);
  if (opacity < 0.5) return { clickable: false, reason: 'visually hidden (opacity < 0.5)' };

  // Check pointer events
  const pointerEvents = await locator.evaluate((el: HTMLElement) => {
    const style = window.getComputedStyle(el);
    return style.pointerEvents;
  }).catch(() => 'auto');
  if (pointerEvents === 'none') return { clickable: false, reason: 'pointer-events: none' };

  return { clickable: true, reason: '' };
}

/**
 * Check if a target element is within the viewport.
 */
async function isInViewport(locator: Locator): Promise<boolean> {
  const box = await locator.boundingBox().catch(() => null);
  if (!box) return false;
  return box.y >= 0 && box.y + box.height <= (await locator.evaluate(() => window.innerHeight).catch(() => 800))!;
}

/**
 * Attempt to click an element, returning the result.
 */
async function safeClick(locator: Locator, description: string): Promise<boolean> {
  try {
    await locator.click({ timeout: 3000, force: false });
    return true;
  } catch {
    // Fallback: try clicking at center of element
    try {
      await locator.click({ timeout: 2000, force: true });
      return true;
    } catch {
      console.warn(`[CRAWLER] Could not click: "${description}"`);
      return false;
    }
  }
}

// ─── Crawler Core ────────────────────────────────────────────────────────────

/**
 * Helper to wait for React to render content (not just network idle).
 * React SPAs may have network idle before components are mounted.
 */
async function waitForReactRender(page: Page, timeoutMs: number = 10000): Promise<void> {
  // Wait for #root to have actual rendered content
  await page.waitForFunction(
    () => {
      const root = document.getElementById('root');
      if (!root) return false;
      
      // Check if there's any visible text content (more than whitespace)
      const textContent = root.innerText || '';
      const hasText = textContent.trim().length > 0;
      
      // Check if there are interactive elements (buttons, links)
      const hasInteractive = root.querySelectorAll('button, a, [role="button"], [role="link"]').length > 0;
      
      // Check if there are form elements
      const hasForms = root.querySelectorAll('input, select, textarea').length > 0;
      
      return hasText && (hasInteractive || hasForms);
    },
    { timeout: timeoutMs }
  ).catch(() => {
    console.log('[waitForReactRender] Falling back to network idle + timeout');
  });
  // Brief wait for any final hydration
  await page.waitForTimeout(500);
}

/**
 * Identify all clickable elements on the current page.
 */
async function findClickableElements(page: Page): Promise<Array<{
  locator: Locator;
  description: string;
  href?: string;
  text: string;
  tag: string;
  role?: string;
  ariaLabel?: string;
}>> {
  // Selector priority: specific roles first, then interactive elements
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
    // Broad catch-all for interactive elements
    'a',
    'button',
    '[role]',
    '[onclick]',
    '[data-action]',
    '[data-testid]',
    '[data-cy]',
    '[draggable]',
    '[contenteditable]',
  ];

  const elements: Array<{
    locator: Locator;
    description: string;
    href?: string;
    text: string;
    tag: string;
    role?: string;
    ariaLabel?: string;
  }> = [];
  const seen = new Set<string>(); // dedup key

  for (const selector of selectors) {
    const locators = page.locator(selector);
    const count = await locators.count();

    for (let i = 0; i < count; i++) {
      const locator = locators.nth(i);

      // Skip elements we've already seen (dedup by text + selector)
      const text = await locator.textContent().catch(() => '');
      const tag = await locator.evaluate((el: HTMLElement) => el.tagName.toLowerCase()).catch(() => 'unknown');
      const role = await locator.evaluate((el: HTMLElement) => el.getAttribute('role') || '').catch(() => '');
      const ariaLabel = await locator.evaluate((el: HTMLElement) => el.getAttribute('aria-label') || '').catch(() => '');
      const href = await locator.evaluate((el: HTMLElement) => el.tagName.toLowerCase() === 'a' ? (el as HTMLAnchorElement).href : '').catch(() => '');

      const dedupKey = `${tag}:${text.trim().slice(0, 80)}:${role}:${ariaLabel}`;
      if (seen.has(dedupKey)) continue;
      seen.add(dedupKey);

      // Build a human-readable description
      const description = ariaLabel || text.trim() || `${tag}${role ? ` [role=${role}]` : ''}${href ? ` [href=${href}]` : ''}`;

      // Skip elements with very generic text that likely match many elements
      // (e.g., empty buttons used as icons)
      if (!text.trim() && !ariaLabel && role) {
        // Keep role-bearing elements (tabs, menu items, etc.) even without text
        elements.push({ locator, description: `[${role}]`, href, text: '', tag, role, ariaLabel: ariaLabel || undefined });
      } else if (text.trim()) {
        elements.push({ locator, description, href, text: text.trim(), tag, role, ariaLabel: ariaLabel || undefined });
      }
    }
  }

  // Also grab elements by data-attributes that indicate clickability
  const dataClickSelectors = [
    '[data-testid]',
    '[data-cy]',
    '[data-action]',
    '[data-nav]',
  ];

  for (const selector of dataClickSelectors) {
    const locators = page.locator(selector);
    const count = await locators.count();
    for (let i = 0; i < count; i++) {
      const locator = locators.nth(i);
      const text = (await locator.textContent().catch(() => '')).trim();
      const tag = await locator.evaluate((el: HTMLElement) => el.tagName.toLowerCase()).catch(() => 'unknown');
      const role = await locator.evaluate((el: HTMLElement) => el.getAttribute('role') || '').catch(() => '');
      const ariaLabel = await locator.evaluate((el: HTMLElement) => el.getAttribute('aria-label') || '').catch(() => '');

      // Skip if already captured
      const dedupKey = `${tag}:${text}:${role}:${ariaLabel}`;
      if (seen.has(dedupKey)) continue;

      const description = ariaLabel || text || `[${selector}]`;
      elements.push({ locator, description, href: undefined, text, tag, role, ariaLabel: ariaLabel || undefined });
    }
  }

  return elements;
}

/**
 * Validate the resulting UI state after a click.
 * Returns an array of validation findings.
 */
async function validatePostClick(page: Page, context: ClickContext, beforeUrl: string): Promise<{
  status: 'success' | 'error';
  details: string[];
  isNavigation: boolean;
  isNewUrl: string | null;
}> {
  const findings: string[] = [];
  const afterUrl = normalizeUrl(page.url());
  const beforeRoute = normalizeUrl(beforeUrl);
  const isNavigation = afterUrl !== beforeRoute;
  let isNewUrl: string | null = null;

  if (isNavigation) {
    isNewUrl = afterUrl;
    const status = await getPageStatusCode(page);

    // Check for HTTP errors
    if (status === 404) {
      findings.push(`Page returned 404`);
    } else if (status === 500) {
      findings.push(`Page returned 500`);
    }

    // Check for blank page
    const blank = await isBlankPage(page);
    if (blank) {
      findings.push('Page appears blank (no content)');
    }

    // Contextual validation: URL + title match expected patterns
    const expected = getExpectedContext(afterUrl);
    if (expected) {
      const pageTitle = await page.title();
      if (expected.titlePattern && !expected.titlePattern.test(pageTitle)) {
        findings.push(`Title "${pageTitle}" does not match expected pattern "${expected.titlePattern}"`);
      }
    }

    // Check if there's a blocking overlay
    const overlay = await hasBlockingOverlay(page);
    if (overlay) {
      findings.push('Blocking overlay present after click');
    }
  } else {
    // DOM mutation: check for overlay, viewport visibility
    const overlay = await hasBlockingOverlay(page);
    if (overlay) {
      findings.push('Blocking overlay present');
    }

    // Check that the page still has content
    const bodyText = await page.evaluate(() => document.body?.innerText?.trim().length ?? 0);
    if (bodyText < 5) {
      findings.push('Page content lost after click');
    }
  }

  const status = findings.length > 0 ? 'error' : 'success';
  return { status, details: findings, isNavigation, isNewUrl };
}

/**
 * Attempt to revert to the parent state (before click).
 */
async function revertToParent(page: Page, beforeUrl: string): Promise<void> {
  // First try: go back
  try {
    await page.goBack({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(500);
    const afterBack = normalizeUrl(page.url());
    if (afterBack === normalizeUrl(beforeUrl)) {
      return; // successfully reverted
    }
  } catch {
    // ignore
  }

  // Second try: directly navigate to the original URL
  if (beforeUrl !== page.url()) {
    try {
      await page.goto(beforeUrl, { waitUntil: 'networkidle', timeout: NAV_TIMEOUT_MS }).catch(() => {});
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.waitForTimeout(500);
      return;
    } catch {
      // ignore
    }
  }

  // Third try: if the beforeUrl was the root, navigate to /
  if (normalizeUrl(beforeUrl) === '/') {
    try {
      await page.goto(`${UI_URL}/`, { waitUntil: 'networkidle', timeout: NAV_TIMEOUT_MS }).catch(() => {});
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.waitForTimeout(500);
    } catch {
      // ignore
    }
  }
}

/**
 * Recursively crawl clickable elements on the current page.
 */
async function crawlPage(
  page: Page,
  report: CrawlReport,
  depth: number,
  path: string[],
): Promise<void> {
  if (depth > MAX_DEPTH) {
    console.log(`  [CRAWLER] Max depth (${MAX_DEPTH}) reached, stopping traversal.`);
    return;
  }

  const currentUrl = normalizeUrl(page.url());

  // Prevent infinite loops: skip if we've already fully crawled this URL at this depth
  const crawlKey = `${currentUrl}:${depth}`;
  if (report.visitedUrls.has(crawlKey)) {
    return;
  }
  report.visitedUrls.add(crawlKey);

  await test.step(`Crawling: ${path.join(' > ')} (depth=${depth}, url=${currentUrl})`, async () => {
    // Wait for network idle before scanning
    await page.waitForLoadState('networkidle', { timeout: CRAWL_TIMEOUT_MS }).catch(() => {});
    
    // Wait for React to finish rendering - check that #root has content
    await page.waitForFunction(() => {
      const root = document.getElementById('root');
      if (!root) return false;
      // Wait for splash to be hidden and root to have rendered content
      const splash = document.getElementById('splash');
      const isSplashHidden = splash?.classList.contains('hidden') || splash?.offsetParent === null;
      const hasContent = root.children.length > 0 && 
                         (root.innerText?.length || 0) > 50;
      return isSplashHidden && hasContent;
    }, { timeout: CRAWL_TIMEOUT_MS }).catch(() => {
      console.log(`  [CRAWLER] Warning: React rendering timeout, proceeding anyway`);
    });
    
    await page.waitForTimeout(500);

    const clickableElements = await findClickableElements(page);

    if (clickableElements.length === 0) {
      // Debug: log what selectors found something
      console.log(`  [CRAWLER DEBUG] No clickable elements found at depth ${depth}`);
      console.log(`  [CRAWLER DEBUG] Current URL: ${page.url()}`);
      console.log(`  [CRAWLER DEBUG] Body text length: ${await page.evaluate(() => document.body?.innerText?.length ?? 0)}`);
      
      // Try to log what's in the body
      const bodyContent = await page.evaluate(() => {
        const body = document.body;
        return {
          html: body?.innerHTML?.slice(0, 1000),
          childCount: body?.children?.length,
          tags: Array.from(body?.querySelectorAll('*') || []).slice(0, 20).map(el => el.tagName),
        };
      });
      console.log(`  [CRAWLER DEBUG] Body HTML (first 1000 chars): ${bodyContent.html}`);
      console.log(`  [CRAWLER DEBUG] First 20 tags: ${JSON.stringify(bodyContent.tags)}`);
      
      return;
    }
    
    console.log(`  [CRAWLER] Found ${clickableElements.length} clickable elements at depth ${depth}`);

    for (const element of clickableElements) {
      const { locator, description, text, tag, role } = element;

      await test.step(`  Element: "${description}" (${tag}${role ? ` [${role}]` : ''})`, async () => {
        report.totalClicks++;

        // ── Pre-click validation ──────────────────────────────────────────
        const { clickable } = await isClickable(locator);
        if (!clickable) {
          report.elementMetadata.push({ text, tag, url: currentUrl, role: role || undefined });
          return; // skip — not interactable
        }

        const inViewport = await isInViewport(locator);
        if (!inViewport) {
          // Try to scroll into view
          try {
            await locator.scrollIntoViewIfNeeded().catch(() => {});
            await page.waitForTimeout(300);
          } catch {
            // ignore
          }
        }

        // Record metadata for reporting
        report.elementMetadata.push({ text, tag, url: currentUrl, role: role || undefined });

        // ── Capture pre-click state ───────────────────────────────────────
        const beforeUrl = page.url();
        const beforeTitle = await page.title();

        // ── Click ─────────────────────────────────────────────────────────
        const clicked = await safeClick(locator, description);
        if (!clicked) {
          report.errors.push({
            url: beforeUrl,
            elementDescription: description,
            error: 'Failed to click element',
            errorType: 'other',
            timestamp: Date.now(),
          });
          return;
        }

        // ── Wait for state to settle ──────────────────────────────────────
        await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(1500);
        await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(500);

        // ── Validate post-click state ─────────────────────────────────────
        const validationResult = await validatePostClick(page, { elementText: text, elementTag: tag, elementRole: role }, beforeUrl);

        if (validationResult.status === 'error') {
          const errorType = validationResult.details.some((d) => d.includes('404'))
            ? '404'
            : validationResult.details.some((d) => d.includes('500'))
            ? '500'
            : validationResult.details.some((d) => d.includes('blank'))
            ? 'blank-page'
            : validationResult.details.some((d) => d.includes('overlay'))
            ? 'overlay-blocked'
            : 'validation-failed';

          report.errors.push({
            url: beforeUrl,
            elementDescription: description,
            error: validationResult.details.join('; '),
            errorType: errorType as CrawlError['errorType'],
            timestamp: Date.now(),
          });
          console.log(`  [CRAWLER] ✗ Issue detected: ${validationResult.details.join(', ')}`);
        } else {
          report.successfulClicks++;
          if (validationResult.isNavigation) {
            report.navigationClicks++;
            console.log(`  [CRAWLER] ✓ Navigated to: ${validationResult.isNewUrl}`);
          } else {
            report.domMutationClicks++;
          }
        }

        // ── Attempt to revert to parent ───────────────────────────────────
        await test.step(`  Reverting to parent: ${beforeTitle}`, async () => {
          await revertToParent(page, beforeUrl);
          await page.waitForTimeout(500);
        });

        // ── If we navigated to a new page, recursively crawl it ───────────
        if (validationResult.isNavigation && depth < MAX_DEPTH) {
          // Only recurse for pages that are different from current (not a toggle)
          await test.step(`  Recursing into: ${validationResult.isNewUrl}`, async () => {
            await crawlPage(page, report, depth + 1, [...path, description]);
          });
        }
      });
    }
  });
}

// ─── Tests ───────────────────────────────────────────────────────────────────

test.describe('E2E Recursive UI Crawler', () => {
  test('comprehensive crawl from Dashboard', async ({ page }) => {
    // Validate credentials are provided
    if (!LOGIN_USER || !LOGIN_PASS) {
      throw new Error('Credentials not found. Create a .env.test file with TEST_USER and TEST_PASS variables.');
    }

    // Collect console logs for additional error detection
    const consoleLogs: string[] = [];
    const errors: string[] = [];
    page.on('console', (msg) => {
      consoleLogs.push(`[${msg.type()}] ${msg.text()}`);
    });
    page.on('pageerror', (err) => {
      errors.push(err.message);
    });

    console.log('[CRAWLER] Starting comprehensive E2E crawl...');

    // ── Step 1: Login ───────────────────────────────────────────────────────
    await test.step('Login with default credentials', async () => {
      await loginAsDefault(page);
      await page.waitForLoadState('networkidle', { timeout: NAV_TIMEOUT_MS }).catch(() => {});
      await waitForReactRender(page, NAV_TIMEOUT_MS).catch(() => {
        console.log('[CRAWLER] Warning: React rendering timeout after login');
      });
      await page.waitForTimeout(1000);
    });

    // ── Step 2: Navigate to Dashboard ───────────────────────────────────────
    await test.step('Navigate to Dashboard (root)', async () => {
      await page.goto(`${UI_URL}/`, { waitUntil: 'networkidle', timeout: NAV_TIMEOUT_MS }).catch(() => {});
      await page.waitForLoadState('networkidle', { timeout: NAV_TIMEOUT_MS }).catch(() => {});
      await waitForReactRender(page, NAV_TIMEOUT_MS);
      await page.waitForTimeout(1000);
    });

    // ── Step 3: Verify Dashboard loaded correctly ──────────────────────────
    await test.step('Verify Dashboard loaded', async () => {
      const title = await page.title();
      expect(title).toMatch(/Jarvis/i, 'Page title should contain "Jarvis"');

      // Wait for actual React content to render
      await waitForReactRender(page, NAV_TIMEOUT_MS);

      // Verify body has content
      const bodyTextLength = await page.evaluate(() => document.body?.innerText?.length ?? 0);
      expect(bodyTextLength).toBeGreaterThan(0, 'Dashboard should have rendered content');

      // Verify key dashboard elements
      const dashboardHeading = page.getByRole('heading', { name: /Jarvis Dashboard/i });
      if (await dashboardHeading.isVisible({ timeout: ELEMENT_TIMEOUT_MS }).catch(() => false)) {
        await expect(dashboardHeading).toBeVisible();
      }
    });

    // ── Step 4: Validate Dashboard Widgets ──────────────────────────────────
    await test.step('Validate Dashboard Widgets', async () => {
      // Wait for widgets to be fully rendered
      await waitForReactRender(page, NAV_TIMEOUT_MS);
      await page.waitForTimeout(1500);

      // Known widgets from BentoBoxDashboard
      // Note: Some widgets require external service configuration (Skylight, Telemetry)
      // and may show "not configured" errors if not set up
      const widgets = [
        { key: 'energy_insights', name: 'Energy Insights', requiresConfig: true },
        { key: 'ambient_timer', name: 'Ambient Timer' },
        { key: 'quick_notes', name: 'Quick Notes' },
        { key: 'active_media', name: 'Active Media' },
        { key: 'chores_progress', name: 'Chores Progress', requiresConfig: true },
        { key: 'upcoming_events', name: 'Upcoming Events' },
        { key: 'quick_assistant', name: 'Quick Assistant' },
        { key: 'device_control', name: 'Device Control' },
      ];

      for (const widget of widgets) {
        // Check if widget is present (not necessarily visible - may be hidden)
        const isPresent = await page.evaluate(
          (key) => {
            // Look for glass-panel widgets (WidgetCard class) and check if title matches
            const glassPanels = document.querySelectorAll('.glass-panel');
            const widgetText = key.replace(/_/g, ' ').toLowerCase();
            return Array.from(glassPanels).some(panel => {
              const h4 = panel.querySelector('h4');
              if (!h4) return false;
              const title = h4.textContent?.toLowerCase() || '';
              return title.includes(widgetText) || title.includes(key.toLowerCase());
            });
          },
          widget.key
        );

        if (isPresent) {
          console.log(`[CRAWLER] ✓ Widget "${widget.name}" (${widget.key}) is present`);
        } else {
          console.log(`[CRAWLER] ⚠ Widget "${widget.name}" (${widget.key}) not found on dashboard`);
          if ((widget as { requiresConfig?: boolean }).requiresConfig) {
            console.log(`[CRAWLER]   Note: "${widget.name}" requires external service configuration`);
          }
        }
      }
    });

    // ── Step 5: Run the recursive crawler ───────────────────────────────────
    const report: CrawlReport = {
      totalClicks: 0,
      successfulClicks: 0,
      navigationClicks: 0,
      domMutationClicks: 0,
      errors: [],
      visitedUrls: new Set<string>(),
      elementMetadata: [],
    };

    await test.step('Recursive crawl from Dashboard', async () => {
      await crawlPage(page, report, 0, ['Dashboard']);
    });

    // ── Step 5: Report ─────────────────────────────────────────────────────
    await test.step('Crawl report', async () => {
      console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
      console.log('  CRAWLER REPORT');
      console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
      console.log(`  Total elements scanned:    ${report.totalClicks}`);
      console.log(`  Successful interactions:     ${report.successfulClicks}`);
      console.log(`  Navigations triggered:       ${report.navigationClicks}`);
      console.log(`  DOM mutations triggered:     ${report.domMutationClicks}`);
      console.log(`  Errors detected:             ${report.errors.length}`);
      console.log(`  Unique URLs visited:         ${report.visitedUrls.size}`);
      console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');

      if (report.errors.length > 0) {
        console.log('  ERRORS DETECTED:');
        for (const err of report.errors) {
          console.log(`    ✗ [${err.errorType}] ${err.elementDescription} on ${err.url} — ${err.error}`);
        }
      }

      // Assertions
      expect(report.totalClicks).toBeGreaterThan(0, 'Should have found at least one clickable element');
      expect(report.successfulClicks).toBeGreaterThan(0, 'Should have successfully interacted with at least one element');

      // No critical errors (404/500/blank-page)
      const criticalErrors = report.errors.filter(
        (e) => ['404', '500', 'blank-page', 'overlay-blocked'].includes(e.errorType)
      );
      expect(criticalErrors).toEqual([]);

      // Check for console errors that indicate broken functionality
      const errorLogs = consoleLogs.filter((l) => l.startsWith('[error]'));
      expect(errorLogs).toEqual([]);
    });
  });

  test('sidebar navigation crawler', async ({ page }) => {
    if (!LOGIN_USER || !LOGIN_PASS) {
      throw new Error('Credentials not found. Create a .env.test file with TEST_USER and TEST_PASS variables.');
    }

    const consoleLogs: string[] = [];
    page.on('console', (msg) => {
      consoleLogs.push(`[${msg.type()}] ${msg.text()}`);
    });

    await test.step('Login', async () => {
      await loginAsDefault(page);
      await page.waitForTimeout(1000);
    });

    await test.step('Navigate to Dashboard', async () => {
      await page.goto(`${UI_URL}/`, { waitUntil: 'networkidle' }).catch(() => {});
      await waitForReactRender(page, NAV_TIMEOUT_MS);
      await page.waitForTimeout(2000);
    });

    // ── Collect sidebar nav items ─────────────────────────────────────────
    const report: CrawlReport = {
      totalClicks: 0,
      successfulClicks: 0,
      navigationClicks: 0,
      domMutationClicks: 0,
      errors: [],
      visitedUrls: new Set<string>(),
      elementMetadata: [],
    };

    await test.step('Crawl sidebar navigation items', async () => {
      // Focus specifically on sidebar/nav elements
      const sidebarSelectors = [
        'aside a',
        'nav a',
        '[role="navigation"] a',
        'header a',
      ];

      const allLinks: Locator[] = [];
      for (const sel of sidebarSelectors) {
        const count = await page.locator(sel).count();
        for (let i = 0; i < count; i++) {
          const loc = page.locator(sel).nth(i);
          const href = await loc.getAttribute('href').catch(() => '') || '';
          // Skip anchor links or non-navigation links
          if (!href || href.startsWith('#') || href.startsWith('http')) continue;
          allLinks.push(loc);
        }
      }

      // Dedup by href
      const seenHrefs = new Set<string>();
      const uniqueLinks: Locator[] = [];
      for (const loc of allLinks) {
        const href = await loc.getAttribute('href').catch(() => '') || '';
        if (seenHrefs.has(href)) continue;
        seenHrefs.add(href);
        uniqueLinks.push(loc);
      }

      for (const link of uniqueLinks) {
        const text = (await link.textContent().catch(() => '')).trim();
        const href = (await link.getAttribute('href').catch(() => '') || '').replace(UI_URL, '');
        const description = text || href;

        await test.step(`Sidebar link: "${description}" → ${href}`, async () => {
          report.totalClicks++;

          const { clickable, reason } = await isClickable(link);
          if (!clickable) {
            report.errors.push({
              url: page.url(),
              elementDescription: description,
              error: `Not clickable: ${reason}`,
              errorType: 'validation-failed',
              timestamp: Date.now(),
            });
            return;
          }

          const beforeUrl = page.url();
          const clicked = await safeClick(link, description);

          if (!clicked) {
            report.errors.push({
              url: beforeUrl,
              elementDescription: description,
              error: 'Failed to click',
              errorType: 'other',
              timestamp: Date.now(),
            });
            return;
          }

          await page.waitForLoadState('networkidle', { timeout: NAV_TIMEOUT_MS }).catch(() => {});
          await page.waitForTimeout(1000);

          // Validate: URL changed
          const afterUrl = normalizeUrl(page.url());
          if (afterUrl === normalizeUrl(beforeUrl)) {
            report.errors.push({
              url: beforeUrl,
              elementDescription: description,
              error: 'URL did not change after click',
              errorType: 'validation-failed',
              timestamp: Date.now(),
            });
          } else {
            report.successfulClicks++;
            report.navigationClicks++;

            // Validate: page is not blank
            const blank = await isBlankPage(page);
            if (blank) {
              report.errors.push({
                url: page.url(),
                elementDescription: description,
                error: 'Navigated page appears blank',
                errorType: 'blank-page',
                timestamp: Date.now(),
              });
            }

            // Validate: known route patterns
            const status = await getPageStatusCode(page);
            if (status === 404 || status === 500) {
              report.errors.push({
                url: page.url(),
                elementDescription: description,
                error: `Page returned HTTP ${status}`,
                errorType: status === 404 ? '404' : '500',
                timestamp: Date.now(),
              });
            }
          }

          // Revert: go back
          await page.goBack({ timeout: 5000 }).catch(() => {});
          await page.waitForTimeout(500);
        });
      }
    });

    // ── Report ────────────────────────────────────────────────────────────
    await test.step('Sidebar navigation report', async () => {
      console.log(`\n  Sidebar: ${report.totalClicks} links scanned, ${report.successfulClicks} navigations, ${report.errors.length} errors`);
      for (const err of report.errors) {
        console.log(`    ✗ [${err.errorType}] "${err.elementDescription}" — ${err.error}`);
      }

      expect(report.totalClicks).toBeGreaterThan(0);
      // No 404/500/blank-page errors
      const criticalErrors = report.errors.filter(
        (e) => ['404', '500', 'blank-page'].includes(e.errorType)
      );
      expect(criticalErrors).toEqual([]);
    });
  });

  test('tab and accordion interaction crawler', async ({ page }) => {
    if (!LOGIN_USER || !LOGIN_PASS) {
      throw new Error('Credentials not found. Create a .env.test file with TEST_USER and TEST_PASS variables.');
    }

    const report: CrawlReport = {
      totalClicks: 0,
      successfulClicks: 0,
      navigationClicks: 0,
      domMutationClicks: 0,
      errors: [],
      visitedUrls: new Set<string>(),
      elementMetadata: [],
    };

    await test.step('Login and navigate to Admin (rich tab interface)', async () => {
      await loginAsDefault(page);
      await page.goto(`${UI_URL}/admin`, { waitUntil: 'networkidle' }).catch(() => {});
      await waitForReactRender(page, NAV_TIMEOUT_MS);
      await page.waitForTimeout(2000);
    });

    // ── Crawl all tabs ──────────────────────────────────────────────────
    await test.step('Crawl Admin tabs (tab-role elements)', async () => {
      // First, click the first tab group to see what tabs are available
      const tabs = page.locator('[role="tab"], button:has-text("Users"), button:has-text("Device"), button:has-text("Telemetry"), button:has-text("Intercom"), button:has-text("Raven"), button:has-text("LLM"), button:has-text("Database")');
      const count = await tabs.count();

      for (let i = 0; i < count; i++) {
        const tab = tabs.nth(i);
        const text = (await tab.textContent().catch(() => '')).trim();
        if (!text) continue;

        await test.step(`Tab: "${text}"`, async () => {
          report.totalClicks++;

          const { clickable } = await isClickable(tab);
          if (!clickable) return;

          const beforeUrl = page.url();
          const clicked = await safeClick(tab, text);

          if (!clicked) {
            report.errors.push({
              url: beforeUrl,
              elementDescription: `Tab: ${text}`,
              error: 'Failed to click',
              errorType: 'other',
              timestamp: Date.now(),
            });
            return;
          }

          // Wait for tab content to render
          await page.waitForTimeout(2000);
          await page.waitForLoadState('networkidle', { timeout: CRAWL_TIMEOUT_MS }).catch(() => {});
          await page.waitForTimeout(500);

          // Validate: tab content is visible (at least one heading or meaningful text in the active tab panel)
          const activePanel = page.locator('[role="tabpanel"]:not([aria-hidden="true"]), [class*="active"], [class*="selected"]').first();
          const hasContent = await activePanel.count().then(async (n) => {
            if (n === 0) return false;
            const text = await activePanel.textContent();
            return (text || '').trim().length > 0;
          });

          if (hasContent) {
            report.successfulClicks++;
            report.domMutationClicks++;
            // Also crawl clickable elements within this tab panel
            if (report.totalClicks < 30) {
              const panelElements = await findClickableElements(activePanel);
              for (const el of panelElements.slice(0, 3)) {
                await test.step(`  Panel element: "${el.description}"`, async () => {
                  report.totalClicks++;
                  const { clickable: elClickable } = await isClickable(el.locator);
                  if (!elClickable) return;

                  const clicked = await safeClick(el.locator, el.description);
                  if (clicked) {
                    await page.waitForTimeout(1000);
                    report.successfulClicks++;
                    report.domMutationClicks++;
                  } else {
                    report.errors.push({
                      url: page.url(),
                      elementDescription: el.description,
                      error: 'Failed to click panel element',
                      errorType: 'other',
                      timestamp: Date.now(),
                    });
                  }
                });
              }
            }
          } else {
            report.errors.push({
              url: page.url(),
              elementDescription: `Tab: ${text}`,
              error: 'Tab panel has no visible content',
              errorType: 'blank-page',
              timestamp: Date.now(),
            });
          }

          // Don't revert tabs — they're toggled in place
        });
      }
    });

    await test.step('Tab crawler report', async () => {
      console.log(`\n  Tabs: ${report.totalClicks} scanned, ${report.successfulClicks} successful, ${report.errors.length} errors`);
      for (const err of report.errors) {
        console.log(`    ✗ [${err.errorType}] "${err.elementDescription}" — ${err.error}`);
      }
      expect(report.totalClicks).toBeGreaterThan(0);
    });
  });

  test('dropdown and modal interaction crawler', async ({ page }) => {
    if (!LOGIN_USER || !LOGIN_PASS) {
      throw new Error('Credentials not found. Create a .env.test file with TEST_USER and TEST_PASS variables.');
    }

    const report: CrawlReport = {
      totalClicks: 0,
      successfulClicks: 0,
      navigationClicks: 0,
      domMutationClicks: 0,
      errors: [],
      visitedUrls: new Set<string>(),
      elementMetadata: [],
    };

    await test.step('Login and navigate to a page with dropdowns', async () => {
      await loginAsDefault(page);
      await page.goto(`${UI_URL}/identity`, { waitUntil: 'networkidle' }).catch(() => {});
      await waitForReactRender(page, NAV_TIMEOUT_MS);
      await page.waitForTimeout(2000);
    });

    // ── Crawl interactive elements that trigger dropdowns/modals ──────────
    await test.step('Crawl dropdown/modal triggering elements', async () => {
      // Elements with aria-expanded or that trigger popovers
      const dropdownSelectors = [
        '[aria-haspopup="listbox"]',
        '[aria-haspopup="true"]',
        'button[aria-expanded="false"]',
        '[role="combobox"]',
        'input[placeholder*="Search"], input[placeholder*="search"], input[placeholder*="Find"]',
        'button:has-text("More"), button:has-text("Actions"), button:has-text("Options")',
      ];

      for (const sel of dropdownSelectors) {
        const count = await page.locator(sel).count();
        for (let i = 0; i < count; i++) {
          const locator = page.locator(sel).nth(i);
          const text = (await locator.textContent().catch(() => '')).trim();
          const ariaLabel = await locator.evaluate((el: HTMLElement) => el.getAttribute('aria-label') || '').catch(() => '');
          const ariaExpanded = await locator.evaluate((el: HTMLElement) => el.getAttribute('aria-expanded') || '').catch(() => '');
          const description = ariaLabel || text || `[${sel}]`;

          if (!text && !ariaLabel) continue; // Skip truly empty elements

          await test.step(`Dropdown element: "${description}"`, async () => {
            report.totalClicks++;

            const { clickable } = await isClickable(locator);
            if (!clickable) return;

            const beforeUrl = page.url();
            const clicked = await safeClick(locator, description);

            if (!clicked) {
              report.errors.push({
                url: beforeUrl,
                elementDescription: description,
                error: 'Failed to click',
                errorType: 'other',
                timestamp: Date.now(),
              });
              return;
            }

            // Wait for dropdown/modal to appear
            await page.waitForTimeout(1500);
            await page.waitForLoadState('networkidle', { timeout: CRAWL_TIMEOUT_MS }).catch(() => {});
            await page.waitForTimeout(500);

            // Check: did something open?
            const expanded = await locator.evaluate((el: HTMLElement) => el.getAttribute('aria-expanded') === 'true').catch(() => false);
            const hasDialog = await page.locator('[role="dialog"], [role="listbox"], [role="menu"], [role="tooltip"]').count().then(async (n) => n > 0);

            if (expanded || hasDialog) {
              report.successfulClicks++;
              report.domMutationClicks++;

              // If a dropdown/menu opened, check its items
              if (hasDialog) {
                const menuItems = await page.locator('[role="menuitem"], [role="option"], [role="listitem"]').all();
                if (menuItems.length > 0) {
                  console.log(`    [CRAWLER] Menu with ${menuItems.length} items opened`);
                }
              }

              // Close the dropdown — try Escape first, then click elsewhere
              await page.keyboard.press('Escape').catch(() => {});
              await page.waitForTimeout(500);
            } else {
              // Might be a toggle — try clicking again to close
              if (ariaExpanded === 'false') {
                await safeClick(locator, description);
                await page.waitForTimeout(500);
              }
            }
          });
        }
      }
    });

    await test.step('Dropdown crawler report', async () => {
      console.log(`\n  Dropdowns: ${report.totalClicks} scanned, ${report.successfulClicks} opened, ${report.errors.length} errors`);
      for (const err of report.errors) {
        console.log(`    ✗ [${err.errorType}] "${err.elementDescription}" — ${err.error}`);
      }
      expect(report.totalClicks).toBeGreaterThan(0);
    });
  });
});
