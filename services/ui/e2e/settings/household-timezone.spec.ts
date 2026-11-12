/**
 * The household time zone control (Admin > Integrations > LLM Configuration),
 * hermetic: every API call is mocked and nothing reaches a server. Runs in
 * every hermetic project, so it is checked on a desktop, phones and tablets.
 *
 * The mocked gateway applies the documented precedence (household setting,
 * then TZ in .env, then the home location when switched on) and refuses a
 * zone that is not IANA, like the real one.
 */
import { expect, test, type Page, type Route } from '@playwright/test';
import { seedAuth } from '../fixtures/mediaMocks';
import { expectNoHorizontalOverflow } from '../fixtures/layoutChecks';

/** Like the server (zoneinfo): any zone the runtime knows, "UTC" included. */
function isIanaZone(zone: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

const ORDER = ['household setting', 'TZ in .env', 'home location'];

interface ZoneState { setting: string; env_tz: string; from_location: boolean; location_set: boolean }

function snapshot(s: ZoneState) {
  const base = { ...s, order: ORDER };
  if (s.setting) return { ...base, zone: s.setting, source: 'household setting', error: null };
  if (s.env_tz) return { ...base, zone: s.env_tz, source: 'TZ in .env', error: null };
  if (s.from_location && s.location_set) return { ...base, zone: 'America/Denver', source: 'home location', error: null };
  return { ...base, zone: null, source: null,
    error: 'No time zone: set the household time zone, TZ in .env, or derive it from the home location.' };
}

async function mockGateway(page: Page, initial: Partial<ZoneState> = {}) {
  const state: ZoneState = { setting: '', env_tz: '', from_location: false, location_set: true, ...initial };
  const puts: Array<Record<string, unknown>> = [];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));

  await seedAuth(page);
  // Everything else answers empty, so no request can leave the browser.
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/') || u.pathname === '/health',
    async (route: Route) => {
      const p = new URL(route.request().url()).pathname;
      if (p === '/api/users/me') return route.fallback();
      if (route.request().method() !== 'GET') return route.fulfill({ json: { status: 'SUCCESS' } });
      if (/\/api\/(health|info)\b/.test(p)) return route.fulfill({ json: { status: 'ok', version: 'e2e', git_sha: 'e2e' } });
      if (/\/api\/(config|calendar\/settings)\b/.test(p)) return route.fulfill({ json: { status: 'SUCCESS', settings: {}, config: {} } });
      if (/\/api\/telemetry\//.test(p)) return route.fulfill({ json: { notifications: [], jobs: [] } });
      return route.fulfill({ json: [] });
    });
  await page.route('**/api/household-timezone', async (route) => {
    const req = route.request();
    if (req.method() === 'PUT') {
      const body = req.postDataJSON() as Record<string, unknown>;
      puts.push(body);
      if (typeof body.timezone === 'string' && body.timezone && !isIanaZone(body.timezone)) {
        return route.fulfill({ status: 400, json: { detail: `Not an IANA time zone: ${body.timezone}` } });
      }
      if (typeof body.timezone === 'string') state.setting = body.timezone;
      if (typeof body.from_location === 'boolean') state.from_location = body.from_location;
    }
    return route.fulfill({ json: snapshot(state) });
  });
  return { puts, errors };
}

async function openControl(page: Page) {
  await page.goto('/admin/integrations');
  const control = page.getByTestId('household-timezone');
  await control.scrollIntoViewIfNeeded();
  await expect(control).toBeVisible();
  return control;
}

test.describe('household time zone', () => {
  test('says when no source gives a zone, then saves a searched one', async ({ page }) => {
    const { puts, errors } = await mockGateway(page);
    const control = await openControl(page);
    const effective = control.getByTestId('household-timezone-effective');
    await expect(effective.getByRole('alert')).toContainText('No time zone');
    await expect(effective).toContainText('TZ in the server .env: not set');

    await control.getByLabel('Search time zones').fill('berlin');
    await control.getByLabel('Household time zone').selectOption('Europe/Berlin');
    await control.getByRole('button', { name: 'Save time zone' }).click();
    await expect(effective).toContainText('In effect: Europe/Berlin (from the household setting)');
    expect(puts).toEqual([{ timezone: 'Europe/Berlin' }]);

    await expectNoHorizontalOverflow(page);
    const buttons = await control.getByRole('button').evaluateAll((els) =>
      els.filter((el) => (el as HTMLElement).offsetParent && el.getBoundingClientRect().height < 43.5)
        .map((el) => (el.textContent || '').trim()));
    expect(buttons, 'buttons shorter than 44px').toEqual([]);
    expect(errors).toEqual([]);
  });

  test('clears back to TZ from .env and can derive from the home location', async ({ page }) => {
    const { puts, errors } = await mockGateway(page, { setting: 'Europe/Berlin', env_tz: 'America/Chicago' });
    const control = await openControl(page);
    const effective = control.getByTestId('household-timezone-effective');
    await expect(effective).toContainText('In effect: Europe/Berlin (from the household setting)');

    await control.getByRole('button', { name: 'Clear (use TZ from .env)' }).click();
    await expect(effective).toContainText('In effect: America/Chicago (from TZ in the server .env)');

    await control.getByRole('switch', { name: /Derive from home location/ }).click();
    await expect(control.getByRole('switch', { name: /Derive from home location/ })).toBeChecked();
    expect(puts).toEqual([{ timezone: '' }, { from_location: true }]);
    expect(errors).toEqual([]);
  });

  test("uses the browser's zone", async ({ page }) => {
    const { puts } = await mockGateway(page);
    const control = await openControl(page);
    const browserZone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
    await control.getByRole('button', { name: `Use my browser's zone (${browserZone})` }).click();
    await control.getByRole('button', { name: 'Save time zone' }).click();
    await expect(control.getByTestId('household-timezone-effective')).toContainText(`In effect: ${browserZone}`);
    expect(puts).toEqual([{ timezone: browserZone }]);
  });
});
