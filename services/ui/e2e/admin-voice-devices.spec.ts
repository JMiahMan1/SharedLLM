import { test, expect as baseExpect, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { controlsOffScreen, expectNoHorizontalOverflow, expectTapTargets, snap } from './fixtures/layoutChecks';

const expect = baseExpect.configure({ timeout: 15_000 });

/**
 * Admin > Users & Devices: the voice satellites, speakers and TVs HomeHub knows,
 * assigned to a person or shared, and voice recognition (the calibration, who is
 * enrolled, candidate samples to review, new voices as counts), and HomeHub's
 * whisper mode, at every hermetic size. Names, devices and scores are made up.
 */

const ADMIN = { id: 1, username: 'pat', display_name: 'Pat', full_name: 'Pat', role: 'admin', is_admin: true, family_role: 'father' };
const USERS = [
  { id: 0, username: 'default', display_name: 'Default', is_system_default: true, role: 'admin', is_admin: true },
  ADMIN,
  { id: 2, username: 'kit', display_name: 'Kit Longname-Example', full_name: 'Kit', role: 'user', is_admin: false, family_role: 'child' },
];

const device = (n: number, over: Record<string, unknown>) => ({
  device_key: `hub:mac:02:00:00:00:00:0${n}`, hardware_id: `mac:02:00:00:00:00:0${n}`, hub_device_id: `d${n}`,
  kind: 'voice_satellite', label: `Test satellite ${n}`, entity_ids: [`assist_satellite.test_${n}`],
  owner_username: null, owner_name: null, assignment: 'shared', room: null, hub_area: 'Den',
  manufacturer: 'Example', model: 'Sat One', last_seen_at: null, archived_at: null, ...over,
});

const DEVICES = [
  device(1, { label: 'Bedroom satellite with a rather long name', owner_username: 'kit', owner_name: 'Kit', assignment: 'person', room: 'Kit room' }),
  device(2, {}),
  device(3, { kind: 'tv', label: 'Test TV', entity_ids: ['media_player.test_tv'] }),
];

const VOICE = {
  calibration: {
    status: 'single', threshold: 0.8, margin: 0.04, people: 1,
    same_speaker: { n: 3, min: 0.877, p10: 0.879, median: 0.889, max: 0.933 }, other_speaker: null,
    per_person: [{ username: 'pat', samples: 3, same_min: 0.877, group: 'adult' }],
  },
  min_samples: 3,
  people: [
    { username: 'pat', name: 'Pat', family_role: 'father', samples: 3, auto_samples: 0, satellite_samples: 0, enrolled: true, consistency: 0.94 },
    { username: 'kit', name: 'Kit Longname-Example', family_role: 'child', samples: 0, auto_samples: 0, satellite_samples: 0, enrolled: false, consistency: null },
  ],
  candidates: [
    { id: 4, username: 'pat', name: 'Pat', score: 0.66, runner_up: null, speech_seconds: 2.2, satellite: 'assist_satellite.test_2', created_at: new Date().toISOString() },
  ],
  new_voices: [
    { id: 'c1', device: 'hub:mac:02:00:00:00:00:02', label: 'Test satellite with a very long room name', visits_week: 5, last_heard: new Date().toISOString() },
  ],
};

const WHISPER = {
  whisper: { enabled: true, volume: 0.22, level_drop_db: 8 },
  defaults: { whisper: { enabled: true, volume: 0.22, level_drop_db: 8 } },
  level_drop_range_db: [3, 30],
};

async function mockApi(page: Page) {
  const patched: unknown[] = [];
  let whisper = { ...WHISPER, whisper: { ...WHISPER.whisper } };
  await page.addInitScript((profile) => {
    localStorage.setItem('jarvis_api_key', 'test-key');
    localStorage.setItem('jarvis_user', JSON.stringify(profile));
  }, ADMIN);
  await page.route((u: URL) => u.pathname.startsWith('/api/') || u.pathname.startsWith('/execute/'), async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/api/users/me') return route.fulfill({ json: ADMIN });
    if (p === '/api/users') return route.fulfill({ json: USERS });
    if (p === '/api/admin/hub-devices') return route.fulfill({ json: { devices: DEVICES } });
    if (p.startsWith('/api/admin/hub-devices/') && req.method() === 'PATCH') {
      const change = req.postDataJSON();
      patched.push(change);
      const key = decodeURIComponent(p.split('/').pop() || '');
      const row = DEVICES.find((d) => d.device_key === key)!;
      return route.fulfill({ json: { ...row, ...change, owner_name: change.owner_username ? 'Kit' : null } });
    }
    if (p === '/api/admin/voice') return route.fulfill({ json: VOICE });
    if (p.startsWith('/api/admin/voice/new_voices/') && req.method() === 'DELETE') {
      patched.push({ forgot: p.split('/').pop(), device: new URL(req.url()).searchParams.get('device') });
      return route.fulfill({ json: { status: 'SUCCESS' } });
    }
    if (p === '/api/dashboard/voice/settings') {
      if (req.method() === 'PUT') {
        const change = req.postDataJSON();
        patched.push(change);
        whisper = { ...whisper, whisper: { ...whisper.whisper, ...change.whisper } };
      }
      return route.fulfill({ json: whisper });
    }
    if (p === '/api/deploy-status') return route.fulfill({ status: 404, body: '' });
    if (p === '/api/integrations/status') return route.fulfill({ json: { homehub: { enabled: true } } });
    if (/\/api\/telemetry\//.test(p)) return route.fulfill({ json: { notifications: [], jobs: [] } });
    if (/\/api\/(config|calendar\/settings)\b/.test(p)) return route.fulfill({ json: { status: 'SUCCESS', settings: {}, config: {} } });
    if (p === '/api/roles') return route.fulfill({ json: { roles: [], permissions: [] } });
    return route.fulfill({ json: [] });
  });
  return patched;
}

test.describe('Admin > Users & Devices: voice', () => {
  test('assign satellites to people and review voice recognition', async ({ page }, info) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const patched = await mockApi(page);
    await page.goto('/admin/users');

    const devices = page.getByTestId('hub-devices-panel');
    await devices.scrollIntoViewIfNeeded();
    await expect(devices.getByTestId('hub-device')).toHaveCount(2);
    await expect(devices.getByLabel('Who uses Bedroom satellite with a rather long name')).toHaveValue('kit');
    const options = await devices.getByLabel('Who uses Test satellite 2').locator('option').allTextContents();
    expect(options).toEqual(['Shared', 'Pat', 'Kit Longname-Example']);
    await devices.getByLabel('Who uses Test satellite 2').selectOption('kit');
    await expect.poll(() => patched).toContainEqual({ owner_username: 'kit' });
    await devices.getByRole('tab', { name: /TVs/ }).click();
    await expect(devices.getByTestId('hub-device')).toHaveCount(1);

    await expectNoHorizontalOverflow(page);
    await expectTapTargets(devices);
    await snap(page, info, 'admin-hub-devices');

    const voice = page.getByTestId('voice-recognition-panel');
    await voice.scrollIntoViewIfNeeded();
    await expect(voice.getByTestId('voice-calibration')).toContainText('80%');
    await expect(voice).toContainText('Only one person has enrolled');
    await expect(voice).toContainText('Not enrolled');
    await expect(voice.getByTestId('voice-candidates')).toContainText('Sounded like');
    await expect(voice.getByTestId('voice-new-voices')).toContainText(
      'A new voice talked to the Test satellite with a very long room name 5 times this week.');
    await expectTapTargets(voice);
    await snap(page, info, 'admin-voice-recognition');
    await voice.getByRole('button', { name: /Dismiss the new voice/ }).click();
    await expect.poll(() => patched).toContainEqual({ forgot: 'c1', device: 'hub:mac:02:00:00:00:00:02' });

    const whisperPanel = page.getByTestId('whisper-mode-panel');
    await whisperPanel.scrollIntoViewIfNeeded();
    const toggle = whisperPanel.getByRole('switch', { name: 'Whisper mode' });
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    await expect(whisperPanel.getByTestId('whisper-volume-value')).toHaveText('22%');
    await whisperPanel.getByLabel('What counts as a whisper').selectOption('12');
    await whisperPanel.getByRole('button', { name: 'Save' }).click();
    await expect.poll(() => patched).toContainEqual({ whisper: { volume: 0.22, level_drop_db: 12 } });
    await expectTapTargets(whisperPanel);
    await snap(page, info, 'admin-whisper-mode');
    await toggle.click();
    await expect.poll(() => patched).toContainEqual({ whisper: { enabled: false } });
    await expect(toggle).toHaveAttribute('aria-checked', 'false');

    expect(await controlsOffScreen(page)).toEqual([]);
    const axe = await new AxeBuilder({ page }).include('[data-testid="hub-devices-panel"]').include('[data-testid="voice-recognition-panel"]').include('[data-testid="whisper-mode-panel"]').analyze();
    expect(axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([]);
    expect(errors).toEqual([]);
  });
});
