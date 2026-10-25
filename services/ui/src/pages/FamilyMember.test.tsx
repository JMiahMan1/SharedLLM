import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactElement } from 'react';
import FamilyMember, { GuardianRedirect } from './FamilyMember';
import TalkRequestCard from '../components/guardian/TalkRequestCard';
import { api } from '../services/api';
import { localDay } from '../lib/healthHub';
import type { GuardianRecap, ScreenTimeStatus } from '../types/api';

const auth = vi.hoisted(() => ({ user: { username: 'dad', is_minor: false } as { username: string; is_minor: boolean } }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../components/geo/LiveFamilyMap', () => ({ default: () => <div data-testid="map" /> }));
vi.mock('../components/bible/ShareToChatSheet', () => ({
  default: ({ buildMessage }: { buildMessage: (n: string) => string }) => <div data-testid="share-sheet">{buildMessage('')}</div>,
}));
vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getFamily: vi.fn(),
      getScreenTimeStatus: vi.fn(),
      getUsage: vi.fn(),
      getAllUserLocations: vi.fn(),
      getGeoZones: vi.fn(),
      getFamilyDirectory: vi.fn(),
      getGuardianRecap: vi.fn(),
      getGuardianGuidance: vi.fn(),
      getGuardianConcerns: vi.fn(),
      getGuardianJournal: vi.fn(),
      addGuardianJournal: vi.fn(),
      getGuardianActivity: vi.fn(),
      getRecommendationLanes: vi.fn(),
      getHealthFamily: vi.fn(),
      getMyParents: vi.fn(),
      askToTalk: vi.fn(),
      getGuardRequests: vi.fn(),
      decideGuardRequest: vi.fn(),
      getScreenTimeRequests: vi.fn(),
      decideScreenTimeRequest: vi.fn(),
      pauseScreenTime: vi.fn(),
      giveScreenTimeBonus: vi.fn(),
      getMemberDevices: vi.fn(),
    },
  };
});

const recap: GuardianRecap = {
  week: { start: '2026-10-19', end: '2026-10-25' }, has_data: true,
  screen: { total_min: 600, daily_avg_min: 150, days_reported: 4, prev_total_min: 400, change_pct: 50, busiest_day: '2026-10-20', busiest_min: 200, late_nights: 3 },
  top_apps: [{ package: 'com.google.android.youtube', minutes: 300 }],
  media: [{ title: 'Lecrae — Blessings', count: 2 }],
  web: { top: [{ domain: 'khanacademy.org', visits: 9 }], blocked: [{ domain: 'tiktok.com', count: 3 }] },
  comms: { calls: 2, call_contacts: 1, messages: 10, message_contacts: 3 },
  faith: { bible_days: 2 },
  concerns: [],
  highlights: ['Read the Bible on 2 days'],
};

const status = (technical: boolean): ScreenTimeStatus => ({
  username: 'sam', day: localDay(), rules: { daily_limit_min: 120 }, can_manage: true, technical,
  limit_min: 120, used_min: 75, bonus_min: 0, remaining_min: 45, bedtime_active: false, school_active: false,
  paused: false, locked: false, reason: null, until: null, resets_at: 'x',
  next_lock_at: new Date(Date.now() + 130 * 60000).toISOString(), next_lock_reason: 'bedtime',
});

function Where() {
  const location = useLocation();
  return <span data-testid="where">{location.pathname}{location.search}</span>;
}

const wrap = (ui: ReactElement, path: string) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/family/member/:username?" element={<>{ui}<Where /></>} />
          <Route path="/guardian" element={<GuardianRedirect />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('The family member page (a parent)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { username: 'dad', is_minor: false };
    vi.mocked(api.getFamily).mockResolvedValue([
      { username: 'dad', display_name: 'Dad', is_minor: false } as never,
      { username: 'emma', display_name: 'Emma', is_minor: true, family_role: 'teen' } as never,
      { username: 'sam', display_name: 'Sam', is_minor: true, family_role: 'child' } as never,
    ]);
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status(true));
    vi.mocked(api.getUsage).mockResolvedValue({ status: 'SUCCESS', days: [{ day: localDay(), millis: 75 * 60000, first_use: null, last_use: null, sessions: 4, apps: [{ package: 'com.google.android.youtube', millis: 40 * 60000 }] }] });
    vi.mocked(api.getAllUserLocations).mockResolvedValue({});
    vi.mocked(api.getGeoZones).mockResolvedValue({ features: [] });
    vi.mocked(api.getFamilyDirectory).mockResolvedValue([]);
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
    vi.mocked(api.getGuardianActivity).mockResolvedValue({ events: [] });
    vi.mocked(api.getRecommendationLanes).mockResolvedValue({ username: 'sam', lanes: {} as never, can_manage: false });
    vi.mocked(api.getHealthFamily).mockResolvedValue({ viewer: 'dad', day: localDay(), people: [] });
    vi.mocked(api.getGuardRequests).mockResolvedValue({ requests: [] });
    vi.mocked(api.getScreenTimeRequests).mockResolvedValue({ requests: [] });
    vi.mocked(api.decideGuardRequest).mockResolvedValue({} as never);
    vi.mocked(api.decideScreenTimeRequest).mockResolvedValue({} as never);
    vi.mocked(api.pauseScreenTime).mockResolvedValue({ rules: {} });
    vi.mocked(api.giveScreenTimeBonus).mockResolvedValue({} as never);
  });

  it('keeps the old /guardian?child= links working', async () => {
    wrap(<FamilyMember />, '/guardian?child=sam');
    expect(await screen.findByTestId('where')).toHaveTextContent('/family/member/sam');
  });

  it("opens the first child when none is named, and shows today, the next routine and concerns with what to say", async () => {
    wrap(<FamilyMember />, '/family/member');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/family/member/emma'));
    expect(await screen.findByTestId('member-today-left')).toHaveTextContent('45 min left today');
    expect(screen.getByTestId('member-next-routine')).toHaveTextContent('Bedtime in 2 h 10 min');
    const concern = await screen.findByTestId('concern');
    expect(concern).toHaveTextContent('Bullying · High');
    expect(concern).toHaveTextContent('In a text message: “nobody likes you”');
    expect(await within(concern).findByText(/Has anyone said anything/)).toBeInTheDocument();
  });

  it("answers only this child's requests, inline", async () => {
    const base = { label: '', note: '', status: 'pending' as const, until: null, decided_by: null, created_at: '2026-10-21T10:00:00Z', decided_at: null };
    vi.mocked(api.getGuardRequests).mockResolvedValue({
      requests: [
        { ...base, id: 1, username: 'sam', kind: 'app', target: 'com.roblox.client' },
        { ...base, id: 3, username: 'sam', kind: 'site', target: 'minecraft.net', note: 'school project' },
        { ...base, id: 4, username: 'emma', kind: 'site', target: 'tiktok.com' },
      ],
    });
    vi.mocked(api.getScreenTimeRequests).mockResolvedValue({
      requests: [{ id: 7, username: 'sam', day: localDay(), minutes: 30, note: 'one more level', status: 'pending', granted_minutes: 0, created_at: '2026-10-21T11:00:00Z' }],
    });
    wrap(<FamilyMember />, '/family/member/sam');
    const card = await screen.findByTestId('open-requests');
    await waitFor(() => expect(card).toHaveTextContent('Installed Roblox'));
    expect(card).toHaveTextContent('Asks for 30 more minutes: “one more level”');
    expect(card).toHaveTextContent('Asks to open minecraft.net: “school project”');
    expect(card).not.toHaveTextContent('tiktok.com');

    fireEvent.click(within(card).getByRole('button', { name: 'Approve app' }));
    await waitFor(() => expect(api.decideGuardRequest).toHaveBeenCalledWith(1, { approve: true }));
    fireEvent.click(within(card).getByRole('button', { name: 'Grant' }));
    await waitFor(() => expect(api.decideScreenTimeRequest).toHaveBeenCalledWith(7, true));
  });

  it('pauses, locks for a while and gives bonus time from the header', async () => {
    wrap(<FamilyMember />, '/family/member/sam');
    fireEvent.click(await screen.findByTestId('member-pause'));
    await waitFor(() => expect(api.pauseScreenTime).toHaveBeenCalledWith('sam', {}));
    fireEvent.click(screen.getByTestId('member-bonus'));
    fireEvent.click(screen.getByRole('button', { name: '30 min' }));
    await waitFor(() => expect(api.giveScreenTimeBonus).toHaveBeenCalledWith('sam', 30));
    fireEvent.click(screen.getByTestId('member-lock'));
    fireEvent.click(screen.getByRole('button', { name: '1 h' }));
    await waitFor(() => expect(api.pauseScreenTime).toHaveBeenLastCalledWith('sam', { until: expect.any(String) }));
  });

  it('shows the father a Devices section and the mother none (the server says which)', async () => {
    const { unmount } = wrap(<FamilyMember />, '/family/member/sam');
    expect(await screen.findByTestId('member-tab-devices')).toBeInTheDocument();
    unmount();

    auth.user = { username: 'mom', is_minor: false };
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status(false));
    wrap(<FamilyMember />, '/family/member/sam?section=devices');
    await screen.findByTestId('member-tab-overview');
    expect(screen.queryByTestId('member-tab-devices')).toBeNull();
    expect(screen.getByTestId('member-panel-overview')).toBeInTheDocument(); // a devices link falls back to the overview
    expect(api.getMemberDevices).not.toHaveBeenCalled();
  });

  it("shows the week, guidance and journal under Faith & growth", async () => {
    wrap(<FamilyMember />, '/family/member/sam?section=faith');
    expect(await screen.findByTestId('guardian-screen')).toHaveTextContent('2 h 30 min');
    expect(await screen.findByTestId('guidance-bullying')).toHaveTextContent('Psalm 139:14');
    fireEvent.change(await screen.findByLabelText('New journal note'), { target: { value: 'Talked on the drive home.' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.addGuardianJournal).toHaveBeenCalledWith('sam', 'Talked on the drive home.', true));
  });

  it('turns a starter into a note the parent edits and sends themselves', async () => {
    wrap(<FamilyMember />, '/family/member/sam?section=faith');
    fireEvent.click(await screen.findByRole('button', { name: /Write Sam a note/ }));
    fireEvent.change(screen.getByLabelText(/Your note to Sam/), { target: { value: 'Love you. Can we talk tonight?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send in chat…' }));
    expect(screen.getByTestId('share-sheet')).toHaveTextContent('Love you. Can we talk tonight?');
  });

  it('explains itself to someone who is not a parent', async () => {
    vi.mocked(api.getFamily).mockRejectedValue(Object.assign(new Error('no'), { isAxiosError: true, response: { status: 403 } }));
    wrap(<FamilyMember />, '/family/member');
    expect(await screen.findByTestId('member-not-a-parent')).toBeInTheDocument();
  });
});

describe('TalkRequestCard (a child)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { username: 'emma', is_minor: true };
    vi.mocked(api.getMyParents).mockResolvedValue({ parents: ['dad', 'mom'] });
    vi.mocked(api.askToTalk).mockResolvedValue({ told: ['mom'] });
  });

  it('tells the parent picked, with a note', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalkRequestCard />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'dad' })); // unpick dad
    fireEvent.change(screen.getByLabelText('A short note'), { target: { value: 'after dinner' } });
    fireEvent.click(screen.getByRole('button', { name: 'Let them know' }));
    await waitFor(() => expect(api.askToTalk).toHaveBeenCalledWith(['mom'], 'after dinner'));
    expect(await screen.findByTestId('talk-request-sent')).toHaveTextContent('mom will find a quiet moment');
  });

  it('is not shown to an adult', () => {
    auth.user = { username: 'dad', is_minor: false };
    const { container } = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalkRequestCard />
      </QueryClientProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
