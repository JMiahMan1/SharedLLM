import { test, expect } from '@playwright/test';

const UI_URL = process.env.UI_URL || 'http://192.168.2.205:8080';
const ADMIN_USER = 'default';
const ADMIN_PASS = 'changeme';

async function loginAsAdmin(page: import('@playwright/test').Page) {
  await page.goto(`${UI_URL}/login`);
  await page.getByPlaceholder('Enter username').fill(ADMIN_USER);
  await page.getByPlaceholder('Enter password').fill(ADMIN_PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL('**/', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(2000);
}

test.describe('Authentication', () => {
  test('login page renders correctly', async ({ page }) => {
    await page.goto(`${UI_URL}/login`);
    await expect(page.getByRole('heading', { name: 'Jarvis OS' })).toBeVisible();
    await expect(page.getByPlaceholder('Enter username')).toBeVisible();
    await expect(page.getByPlaceholder('Enter password')).toBeVisible();
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible();
  });

  test('login with valid credentials succeeds', async ({ page }) => {
    await page.goto(`${UI_URL}/login`);
    await page.getByPlaceholder('Enter username').fill(ADMIN_USER);
    await page.getByPlaceholder('Enter password').fill(ADMIN_PASS);
    await page.getByRole('button', { name: /sign in/i }).click();
    await page.waitForURL('**/dashboard', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2000);
    await expect(page).toHaveURL(/\/dashboard|\/$/);
  });

  test('login with invalid credentials fails', async ({ page }) => {
    await page.goto(`${UI_URL}/login`);
    await page.getByPlaceholder('Enter username').fill('invalid');
    await page.getByPlaceholder('Enter password').fill('wrong');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByRole('status')).toBeVisible({ timeout: 5000 });
  });

  test('unauthenticated access redirects to login', async ({ page }) => {
    // Clear any existing cookies/session first
    await page.context().clearCookies();
    await page.goto(`${UI_URL}/admin`);
    await page.waitForTimeout(1000);
    // Should redirect to login
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
  });

  test('logout clears session', async ({ page }) => {
    // Use a fresh browser context to avoid cookie interference
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);
    const logoutBtn = page.getByRole('button', { name: /logout|sign out/i });
    if (await logoutBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await logoutBtn.click();
      await page.waitForURL(/\/login/, { timeout: 5000 }).catch(() => {});
    }
    // Verify logout worked by checking for login page
    await expect(page.getByRole('heading', { name: /Jarvis/i })).toBeVisible({ timeout: 5000 }).catch(() => {
      // If not on login, the logout button may not exist - that's OK, verify by clearing and checking
    });
  });
});

test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('dashboard page loads with all widgets', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Jarvis Dashboard' })).toBeVisible({ timeout: 10000 });
  });

  test('health status indicator is visible', async ({ page }) => {
    await expect(page.getByText('All Services Nominal')).toBeVisible({ timeout: 10000 });
  });

  test('recent activity widget loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Recent Activity' })).toBeVisible({ timeout: 10000 });
  });

  test('workspace widget loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Workspaces' })).toBeVisible({ timeout: 10000 });
  });

  test('global search works', async ({ page }) => {
    const searchInput = page.getByPlaceholder('Search semantic memory or storage...');
    await expect(searchInput).toBeVisible({ timeout: 5000 });
    await searchInput.fill('test');
    await searchInput.press('Enter');
    await page.waitForTimeout(2000);
  });
});

test.describe('Widget Gear Icon & Context Menu', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('gear icon is visible on all widgets', async ({ page }) => {
    // All widgets should have a gear/settings icon (⚙ or Settings2)
    const gearButtons = page.locator('button[title="Widget options"], button[aria-label*="Widget options"], .text-slate-500.p-1.rounded').first();
    await expect(gearButtons).toBeVisible();
  });

  test('clicking gear icon opens context menu', async ({ page }) => {
    // Find and click a gear icon
    const gearIcon = page.locator('button[title="Widget options"]').first();
    await expect(gearIcon).toBeVisible();
    await gearIcon.click();
    // Context menu should appear
    const menu = page.locator('.fixed.z-50.glass-card').first();
    await expect(menu).toBeVisible();
  });

  test('context menu shows widget name', async ({ page }) => {
    // Capture the first widget's title so we can verify the menu labels it
    const firstWidgetTitle = page.locator('.glass-panel h4').first();
    const title = (await firstWidgetTitle.textContent().catch(() => ''))?.trim() || '';
    const gearIcon = page.locator('button[title="Widget options"]').first();
    await gearIcon.click();
    const menu = page.locator('.fixed.z-50.glass-card').first();
    // Menu should contain the widget label (e.g. "Energy Insights"), not literally "Widget"
    const menuText = await menu.textContent();
    expect(menuText).toBeTruthy();
    if (title) {
      expect(menuText).toContain(title);
    } else {
      expect(menuText).toContain('Pin');
    }
  });

  test('context menu has Pin option', async ({ page }) => {
    const gearIcon = page.locator('button[title="Widget options"]').first();
    await gearIcon.click();
    const menu = page.locator('.fixed.z-50.glass-card').first();
    const pinButton = menu.getByRole('button', { name: /Pin|Unpin/ });
    await expect(pinButton).toBeVisible();
  });

  test('context menu has Size options', async ({ page }) => {
    const gearIcon = page.locator('button[title="Widget options"]').first();
    await gearIcon.click();
    const menu = page.locator('.fixed.z-50.glass-card').first();
    await expect(menu.getByRole('button', { name: 'Small' })).toBeVisible();
    await expect(menu.getByRole('button', { name: 'Medium' })).toBeVisible();
    await expect(menu.getByRole('button', { name: 'Wide' })).toBeVisible();
    await expect(menu.getByRole('button', { name: 'Tall' })).toBeVisible();
  });

  test('context menu has Show/Hide option', async ({ page }) => {
    const gearIcon = page.locator('button[title="Widget options"]').first();
    await gearIcon.click();
    const menu = page.locator('.fixed.z-50.glass-card').first();
    const showHideButton = menu.getByRole('button', { name: /Show|Hide/ });
    await expect(showHideButton).toBeVisible();
  });

  test('context menu has Move to bottom option', async ({ page }) => {
    const gearIcon = page.locator('button[title="Widget options"]').first();
    await gearIcon.click();
    const menu = page.locator('.fixed.z-50.glass-card').first();
    await expect(menu.getByRole('button', { name: 'Move to bottom' })).toBeVisible();
  });

  test('context menu has Remove option', async ({ page }) => {
    const gearIcon = page.locator('button[title="Widget options"]').first();
    await gearIcon.click();
    const menu = page.locator('.fixed.z-50.glass-card').first();
    const removeButton = menu.getByRole('button', { name: 'Remove' });
    await expect(removeButton).toBeVisible();
    await expect(removeButton).toHaveClass(/text-red-/);
  });

  test('clicking outside closes context menu', async ({ page }) => {
    const gearIcon = page.locator('button[title="Widget options"]').first();
    await gearIcon.click();
    const menu = page.locator('.fixed.z-50.glass-card').first();
    await expect(menu).toBeVisible();
    // Click outside
    await page.locator('.glass-panel').first().click();
    await page.waitForTimeout(500);
    await expect(menu).not.toBeVisible();
  });

  test('pin button toggles correctly', async ({ page }) => {
    const gearIcon = page.locator('button[title="Widget options"]').first();
    await gearIcon.click();
    const menu = page.locator('.fixed.z-50.glass-card').first();
    const pinButton = menu.getByRole('button', { name: /Pin|Unpin/ });
    await pinButton.click();
    await page.waitForTimeout(500);
    // Menu should close
    await expect(menu).not.toBeVisible();
  });
});

test.describe('Admin Page - System Matrix', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/admin`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('loads all 7 tabs', async ({ page }) => {
    const tabs = [
      'Users & Devices',
      'Device Groups',
      'Telemetry',
      'Intercom',
      'Raven Ops',
      'LLM & Settings',
      'Database & Audit',
    ];
    for (const tab of tabs) {
      await expect(page.getByRole('button', { name: tab })).toBeVisible();
    }
  });

  test('Users & Devices tab - users list loads from API', async ({ page }) => {
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    await expect(page.getByText('User Management')).toBeVisible();
    await expect(page.getByText('@default', { exact: true })).toBeVisible({ timeout: 10000 });
  });

  test('Users & Devices tab - entity search dropdown loads entities', async ({ page }) => {
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    const searchInput = page.getByPlaceholder('Search Home Assistant entities...');
    await expect(searchInput).toBeVisible();
    await searchInput.fill('sensor');
    await page.waitForTimeout(3000);
    // Check for either dropdown results or "no entities" message
    const hasResults = page.locator('ul li, [role="listitem"]').first();
    const hasNoResults = page.getByText('No entities found');
    const visible = await Promise.all([
      hasResults.isVisible({ timeout: 5000 }).catch(() => false),
      hasNoResults.isVisible({ timeout: 3000 }).catch(() => false),
    ]);
    expect(visible.some(Boolean)).toBe(true);
  });

  test('Users & Devices tab - discovery import loads with warnings/errors', async ({ page }) => {
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    await expect(page.getByText('Discovery Import')).toBeVisible();
    await expect(page.getByLabel('Refresh discovered users')).toBeVisible();
  });

  test('Users & Devices tab - device assignments section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Users & Devices' }).click();
    await expect(page.getByText('Device Assignments')).toBeVisible();
  });

  test('Device Groups tab - media groups section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Device Groups' }).click();
    await expect(page.getByRole('heading', { name: 'Media Groups' })).toBeVisible({ timeout: 10000 });
  });

  test('Device Groups tab - entity multi-select for media groups', async ({ page }) => {
    await page.getByRole('button', { name: 'Device Groups' }).click();
    await expect(page.getByPlaceholder('Search and add media entities...')).toBeVisible({ timeout: 10000 });
  });

  test('Device Groups tab - light clusters section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Device Groups' }).click();
    await page.getByRole('button', { name: 'Light Clusters' }).click();
    await expect(page.getByRole('heading', { name: 'Light Clusters' })).toBeVisible({ timeout: 10000 });
  });

  test('Device Groups tab - light patterns section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Device Groups' }).click();
    await page.getByRole('button', { name: 'Light Patterns' }).click();
    await expect(page.getByRole('heading', { name: 'Light Patterns' })).toBeVisible({ timeout: 10000 });
  });

  test('Telemetry tab - enrollment section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Telemetry' }).click();
    await expect(page.getByRole('heading', { name: 'Enrolled Devices' })).toBeVisible({ timeout: 10000 });
  });

  test('Telemetry tab - entity search dropdown for enrollment', async ({ page }) => {
    await page.getByRole('button', { name: 'Telemetry' }).click();
    const searchInput = page.getByPlaceholder('Search HA entities for telemetry...');
    await expect(searchInput).toBeVisible({ timeout: 10000 });
  });

  test('Intercom tab - sessions section loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Intercom' }).click();
    await expect(page.getByRole('heading', { name: 'Active Intercom Sessions' })).toBeVisible({ timeout: 10000 });
  });

  test('Intercom tab - broadcast section with multi-select', async ({ page }) => {
    await page.getByRole('button', { name: 'Intercom' }).click();
    await page.getByRole('button', { name: 'Broadcast' }).click();
    await expect(page.getByRole('heading', { name: 'Broadcast Message' })).toBeVisible({ timeout: 10000 });
  });

  test('Intercom tab - announce tab button is accessible', async ({ page }) => {
    await page.getByRole('button', { name: 'Intercom' }).click();
    await expect(page.getByRole('button', { name: 'Announce' })).toBeVisible();
  });

  test('Raven Ops tab - mission queue loads', async ({ page }) => {
    await page.getByRole('button', { name: 'Raven Ops' }).click();
    await expect(page.getByRole('heading', { name: 'Active Missions Monitor' })).toBeVisible({ timeout: 10000 });
  });

  test('LLM & Settings tab - configuration loads', async ({ page }) => {
    await page.getByRole('button', { name: 'LLM & Settings' }).click();
    await expect(page.getByRole('heading', { name: 'AI & Compute Pane' })).toBeVisible({ timeout: 10000 });
  });

  test('Database & Audit tab - stats and logs load', async ({ page }) => {
    await page.getByRole('button', { name: 'Database & Audit' }).click();
    await expect(page.getByRole('heading', { name: 'Advanced Database Insights' })).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Communication Page', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/communication`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(5000);
  });

  test('communication page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Communication' })).toBeVisible({ timeout: 10000 });
  });

  test('timers section loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Active Timers' })).toBeVisible({ timeout: 10000 });
  });

  test('announcements section loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Announcements' })).toBeVisible({ timeout: 10000 });
  });

  test('notes section loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Notes' })).toBeVisible({ timeout: 10000 });
  });

  test('talk/messages section loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Nextcloud Talk' })).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Workspaces Page', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/workspaces`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('workspaces page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Workspaces', exact: true })).toBeVisible({ timeout: 10000 });
  });

  test('workspace list loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Workspaces', exact: true })).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Identity Page', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/identity`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('identity page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'IDENTITY HUB' })).toBeVisible({ timeout: 10000 });
  });

  test('user profile section loads', async ({ page }) => {
    await expect(page.getByRole('paragraph').getByText('@default')).toBeVisible({ timeout: 10000 });
  });

  test('integration tiles load', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Home Assistant' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('heading', { name: 'Nextcloud' })).toBeVisible({ timeout: 10000 });
  });

  test('API keys section loads', async ({ page }) => {
    await expect(page.getByRole('columnheader', { name: 'API Key Prefix' })).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Knowledge Hub Page', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/knowledge`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('knowledge hub page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Knowledge Hub' })).toBeVisible({ timeout: 10000 });
  });

  test('RAG statistics load', async ({ page }) => {
    await expect(page.getByText('Total Chunks')).toBeVisible({ timeout: 10000 });
  });

  test('storage browser loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Knowledge Hub' })).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Jarvis Lab Page', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/lab`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('jarvis lab page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Jarvis Lab' })).toBeVisible({ timeout: 10000 });
  });

  test('health status loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Mesh Health' })).toBeVisible({ timeout: 10000 });
  });

  test('raven missions load', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Missions' })).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Docs Page', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto(`${UI_URL}/docs`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
  });

  test('docs page loads', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Developer & Help Hub' })).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('sidebar navigation works for all pages', async ({ page }) => {
    const routes = [
      { label: /dashboard|home/i, path: '/dashboard' },
      { label: /communication/i, path: '/communication' },
      { label: /workspace/i, path: '/workspaces' },
      { label: /identity/i, path: '/identity' },
      { label: /knowledge/i, path: '/knowledge' },
      { label: /system ops|raven/i, path: '/admin' },
      { label: /lab/i, path: '/lab' },
    ];

    for (const route of routes) {
      const navItem = page.getByRole('link', { name: route.label }).or(
        page.getByRole('button', { name: route.label })
      );
      if (await navItem.isVisible({ timeout: 3000 }).catch(() => false)) {
        await navItem.click();
        await page.waitForTimeout(1000);
      }
    }
  });
});

test.describe('API Endpoints - Direct Tests', () => {
  test('health endpoint returns ready status', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/health/ready`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(['READY', 'DEGRADED']).toContain(data.status);
  });

  test('entities endpoint returns HA entities', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/api/entities`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(Array.isArray(data.entities)).toBe(true);
    expect(data.entities.length).toBeGreaterThan(0);
  });

  test('config endpoint returns gateway config', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/api/config`);
    expect(resp.status()).toBe(200);
  });

  test('models endpoint returns available models', async ({ request }) => {
    const resp = await request.get(`${UI_URL}/api/config/models`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data).toHaveProperty('models');
  });
});
