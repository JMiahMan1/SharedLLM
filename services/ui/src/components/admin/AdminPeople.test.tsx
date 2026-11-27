import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AdminPeople from './AdminPeople';
import { api } from '../../services/api';
import type { AdminPersonStats } from '../../lib/adminPeople';
import { ago, newestVersion, percent, personFromPath, problemText } from '../../lib/adminPeople';

vi.mock('../../services/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  api: { getAdminPersonStats: vi.fn(), getFamilyDirectory: vi.fn(), getAvatarImage: vi.fn() },
}));

const STATS: AdminPersonStats = {
  username: 'kit',
  person: { username: 'kit', display_name: 'Kit' },
  profile: { username: 'kit', own: true, person: { username: 'kit', display_name: 'Kit' }, cards: [
    { area: 'garden', state: 'ok', data: { streak_current: 2, streak_longest: 4, care_logs: 9, achievements: [], points: 5 } },
  ] },
  flags: [
    { area: 'bible', level: 'watch', text: 'Bible reading streak has stopped (best was 12 days).' },
    { area: 'jarvis', level: 'fix', text: '1 thing asked for that Jarvis cannot do yet.' },
  ],
  sections: {
    usage: { state: 'ok', data: {
      devices: [
        { label: "Kit's phone", kind: 'phone', model: 'Phone 1', app_version: '2.0.0', last_seen_at: new Date().toISOString() },
        { label: 'Old tablet', kind: 'tablet', app_version: '1.9.0', last_seen_at: new Date().toISOString() },
      ],
      features: [{ feature: 'bible', kind: 'app', uses: 40, last_used_at: new Date().toISOString() },
        { feature: 'recipes', kind: 'app', uses: 3, last_used_at: new Date(Date.now() - 60 * 86400000).toISOString() }],
      capability_gaps: [{ capability: 'set_oven', kind: 'assistant', request: 'preheat the oven', occurrences: 3, resolved: false }],
      conversations: { total: 12, per_day: [{ day: '2026-11-23', count: 5 }, { day: '2026-11-24', count: 7 }], features: [{ feature: 'weather', count: 6 }] },
      events: { app_open: 30 }, app_opens_per_day: [{ day: '2026-11-24', count: 4 }],
    } },
    collector: { state: 'ok', data: { devices: [{ device_name: "Kit's phone", problems: [{ kind: 'permission', what: 'read_sms' }] }], staleness: {}, problem_count: 1 } },
    features: { state: 'ok', data: { since: '2026-10-25', counts: { media: 9, bible: 4 } } },
    screen: { state: 'ok', data: { days: [{ day: '2026-11-23', minutes: 90, sessions: 10 }, { day: '2026-11-24', minutes: 120, sessions: 12 }] } },
    guardian: { state: 'ok', data: { concerns: 2, levels: { high: 1, low: 1 } } },
    logs: { state: 'unavailable', reason: 'This part of Jarvis is not answering.' },
    voice: { state: 'ok', data: null },
  },
};

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/admin/*" element={<AdminPeople />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.getAdminPersonStats).mockReset().mockResolvedValue(STATS);
  vi.mocked(api.getFamilyDirectory).mockResolvedValue([
    { username: 'kit', display_name: 'Kit', nextcloud_user: null, avatar: null },
    { username: 'pat', display_name: 'Pat', nextcloud_user: null, avatar: null },
    { username: 'default', display_name: 'Household', nextcloud_user: null, avatar: null },
  ]);
});

describe('Admin > People', () => {
  it("opens the person in the path, with what needs a look first", async () => {
    renderAt('/admin/people/kit');
    expect(await screen.findByTestId('admin-flags')).toBeTruthy();
    expect(api.getAdminPersonStats).toHaveBeenCalledWith('kit');
    expect(screen.getByTestId('admin-flags').textContent).toMatch(/streak has stopped/);
    expect(screen.queryByTestId('admin-person-default')).toBeNull();
  });

  it('shows devices with an out-of-date app, and collection problems in plain words', async () => {
    renderAt('/admin/people/kit');
    const devices = await screen.findByTestId('admin-devices');
    expect(within(devices).getByText('app 1.9.0').className).toMatch(/amber/);
    expect(within(devices).getByTestId('admin-collector-problems').textContent).toMatch(/Permission off: read sms/);
  });

  it('counts conversations without showing them, and lists what was asked for', async () => {
    renderAt('/admin/people/kit');
    expect((await screen.findByTestId('admin-assistant')).textContent).toMatch(/counted, not read/);
    expect(screen.getByTestId('admin-gaps').textContent).toMatch(/set_oven/);
    expect(screen.getByTestId('admin-features-dropped').textContent).toMatch(/recipes/);
  });

  it('greys a section that did not answer, and keeps the rest', async () => {
    renderAt('/admin/people/kit');
    expect((await screen.findByTestId('admin-logs-error')).textContent).toMatch(/not answering/);
    expect(screen.getByTestId('admin-voice-empty')).toBeTruthy();
    expect(screen.getByTestId('profile-card-garden')).toBeTruthy();
  });

  it('shows how requests went: failures, waits, the tools that failed and the slow hours', async () => {
    vi.mocked(api.getAdminPersonStats).mockResolvedValue({ ...STATS, sections: { ...STATS.sections, voice: { state: 'ok', data: {
      days: 30, total: 40, outcomes: { ok: 30, timeout: 4, offline: 2, tool_failure: 4, queued: 5 },
      latency: { buckets: {}, p50_ms: 2000, p90_ms: 8000 },
      per_day: [{ day: '2026-11-23', total: 20, ok: 15, failed: 5 }, { day: '2026-11-24', total: 20, ok: 15, failed: 5 }],
      tools: [{ tool: 'set_oven', failures: 4, calls: 5 }], slow_hours: [{ hour: 7, slow: 3, total: 6 }],
      identity: { identified: 6, 'device-assigned': 9, default: 0 },
    } } } });
    renderAt('/admin/people/kit');
    const card = await screen.findByTestId('admin-voice');
    expect(card.textContent).toMatch(/10 \(25%\)/);
    expect(card.textContent).toMatch(/Fell back: AI offline: 2/);
    expect(card.textContent).toMatch(/Waited in line: 5/);
    expect(card.textContent).toMatch(/set_oven/);
    expect(card.textContent).toMatch(/07:00 \(3 of 6 slow\)/);
    const identity = screen.getByTestId('admin-voice-identity');
    expect(identity.textContent).toMatch(/Recognised by voice: 6/);
    expect(identity.textContent).toMatch(/By their satellite: 9/);
    expect(identity.textContent).not.toMatch(/default user/);
  });

  it('picks the first person when none is named', async () => {
    renderAt('/admin/people');
    await screen.findByTestId('admin-person-stats');
    expect(api.getAdminPersonStats).toHaveBeenCalledWith('kit');
  });
});

describe('admin people helpers', () => {
  it('reads the person from the path', () => {
    expect(personFromPath('/admin/people/Kit')).toBe('kit');
    expect(personFromPath('/admin/people')).toBeNull();
  });
  it('finds the newest app version numerically', () => {
    expect(newestVersion([{ app_version: '1.10.0' }, { app_version: '1.9.2' }])).toBe('1.10.0');
    expect(newestVersion([])).toBeNull();
  });
  it('says times and shares plainly', () => {
    const now = Date.parse('2026-11-24T12:00:00Z');
    expect(ago('2026-11-24T11:30:00Z', now)).toBe('30 min ago');
    expect(ago(null)).toBe('never');
    expect(percent(1, 4)).toBe('25%');
    expect(percent(1, 0)).toBe('-');
    expect(problemText({ kind: 'worker', what: 'comms_sync', state: 'CANCELLED' })).toBe('Background job comms sync is cancelled');
  });
});
