import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactElement } from 'react';
import Guardian from './Guardian';
import { api } from '../services/api';
import { localDay } from '../lib/healthHub';
import type { GuardianRecap, ScreenTimeStatus } from '../types/api';

const auth = vi.hoisted(() => ({ user: { username: 'dad', is_minor: false } as { username: string; is_minor: boolean } }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getFamily: vi.fn(),
      getScreenTimeStatus: vi.fn(),
      getUsage: vi.fn(),
      getGuardianRecap: vi.fn(),
      getGuardianGuidance: vi.fn(),
      getGuardianConcerns: vi.fn(),
      getGuardianJournal: vi.fn(),
      addGuardianJournal: vi.fn(),
      getGuardRequests: vi.fn(),
      decideGuardRequest: vi.fn(),
      getScreenTimeRequests: vi.fn(),
      decideScreenTimeRequest: vi.fn(),
      setScreenRules: vi.fn(),
      getMemberDevices: vi.fn(),
      getMyParents: vi.fn(),
      askToTalk: vi.fn(),
    },
  };
});

const recap: GuardianRecap = {
  week: { start: '2026-10-19', end: '2026-10-25' }, has_data: true,
  screen: { total_min: 600, daily_avg_min: 150, days_reported: 4, prev_total_min: 400, change_pct: 50, busiest_day: '2026-10-20', busiest_min: 200, late_nights: 3 },
  top_apps: [{ package: 'com.google.android.youtube', minutes: 300 }],
  media: [],
  web: { top: [], blocked: [] },
  comms: { calls: 2, call_contacts: 1, messages: 10, message_contacts: 3 },
  faith: { bible_days: 2 },
  concerns: [],
  highlights: [],
};

const status = (technical: boolean): ScreenTimeStatus => ({
  username: 'sam', day: localDay(), rules: { daily_limit_min: 120 }, can_manage: true, technical,
  limit_min: 120, used_min: 75, bonus_min: 0, remaining_min: 45, bedtime_active: false, school_active: false,
  paused: false, locked: false, reason: null, until: null, resets_at: 'x',
  next_lock_at: null, next_lock_reason: null,
});

function Where() {
  const location = useLocation();
  return <span data-testid="where">{location.pathname}{location.search}</span>;
}

const wrap = (ui: ReactElement, path = '/guardian') =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/guardian" element={<>{ui}<Where /></>} />
          <Route path="/family/member/:username?" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('The family guardian page (a parent)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { username: 'dad', is_minor: false };
    vi.mocked(api.getFamily).mockResolvedValue([
      { username: 'dad', display_name: 'Dad', is_minor: false } as never,
      { username: 'emma', display_name: 'Emma', is_minor: true, family_role: 'teen' } as never,
      { username: 'sam', display_name: 'Sam', is_minor: true, family_role: 'child' } as never,
    ]);
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status(true));
    vi.mocked(api.getGuardianRecap).mockResolvedValue({ recap });
    vi.mocked(api.getGuardianGuidance).mockResolvedValue({
      week: recap.week,
      prompts: [{ signal: 'bullying', title: 'Unkind words', ref: 'Psalm 139:14', verse: 'I will praise thee', prayer: 'Pray for Sam.', starter: 'Has anyone said anything to you lately that hurt?' }],
    });
    vi.mocked(api.getGuardianConcerns).mockResolvedValue({
      concerns: [{ category: 'bullying', label: 'Bullying', level: 'high', excerpt: 'nobody likes you', feature: 'sms', at: new Date().toISOString() }],
    });
    vi.mocked(api.getGuardianJournal).mockResolvedValue({ notes: [] });
    vi.mocked(api.addGuardianJournal).mockResolvedValue({} as never);
    vi.mocked(api.getGuardRequests).mockResolvedValue({ requests: [] });
    vi.mocked(api.getScreenTimeRequests).mockResolvedValue({ requests: [] });
    vi.mocked(api.decideGuardRequest).mockResolvedValue({} as never);
    vi.mocked(api.decideScreenTimeRequest).mockResolvedValue({} as never);
    vi.mocked(api.setScreenRules).mockResolvedValue({ rules: {} });
    vi.mocked(api.getMemberDevices).mockResolvedValue({ username: 'emma', devices: [], tamper: [], offline_code: {} });
  });

  it('gathers the guarding cards for the first child', async () => {
    wrap(<Guardian />);
    expect(await screen.findByTestId('guardian-page')).toBeInTheDocument();
    expect(await screen.findByTestId('guardian-screen')).toHaveTextContent('2 h 30 min');
    expect(await screen.findByTestId('guidance-bullying')).toHaveTextContent('Psalm 139:14');
    expect(await screen.findByTestId('guardian-journal')).toBeInTheDocument();
    expect(await screen.findByTestId('member-concerns')).toHaveTextContent('Bullying');
    expect(await screen.findByTestId('open-requests')).toBeInTheDocument();
    expect(await screen.findByTestId('screen-time-controls')).toBeInTheDocument();
    expect(await screen.findByTestId('member-devices')).toBeInTheDocument();
    expect(screen.getByTestId('guardian-full-page')).toHaveAttribute('href', '/family/member/emma');
  });

  it('switches between children', async () => {
    wrap(<Guardian />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sam' }));
    await waitFor(() => expect(screen.getByTestId('guardian-full-page')).toHaveAttribute('href', '/family/member/sam'));
    expect(await screen.findByTestId('guardian-screen')).toBeInTheDocument();
  });

  it('keeps the old /guardian?child= links working', async () => {
    wrap(<Guardian />, '/guardian?child=sam');
    expect(await screen.findByTestId('where')).toHaveTextContent('/family/member/sam');
  });

  it('explains itself to someone who is not a parent', async () => {
    vi.mocked(api.getFamily).mockRejectedValue(Object.assign(new Error('no'), { isAxiosError: true, response: { status: 403 } }));
    wrap(<Guardian />);
    expect(await screen.findByTestId('guardian-not-a-parent')).toBeInTheDocument();
  });

  it('says plainly when there are no children yet', async () => {
    vi.mocked(api.getFamily).mockResolvedValue([{ username: 'dad', display_name: 'Dad', is_minor: false } as never]);
    wrap(<Guardian />);
    expect(await screen.findByTestId('guardian-no-children')).toBeInTheDocument();
  });
});

describe('The family guardian page (a child)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { username: 'emma', is_minor: true };
    vi.mocked(api.getMyParents).mockResolvedValue({ parents: ['dad', 'mom'] });
    vi.mocked(api.askToTalk).mockResolvedValue({ told: ['mom'] });
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue({ ...status(false), can_manage: false });
    vi.mocked(api.getGuardRequests).mockResolvedValue({ requests: [] });
    vi.mocked(api.getScreenTimeRequests).mockResolvedValue({ requests: [] });
  });

  it('shows their own guarding tools, not the family list', async () => {
    wrap(<Guardian />);
    expect(await screen.findByTestId('guardian-page')).toBeInTheDocument();
    expect(await screen.findByTestId('talk-request')).toBeInTheDocument();
    expect(screen.queryByTestId('guardian-screen')).toBeNull();
    expect(api.getFamily).not.toHaveBeenCalled();
  });
});
