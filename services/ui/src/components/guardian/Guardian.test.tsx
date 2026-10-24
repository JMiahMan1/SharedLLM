import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import Guardian from '../../pages/Guardian';
import TalkRequestCard from './TalkRequestCard';
import { api } from '../../services/api';
import type { GuardianRecap } from '../../types/api';

const auth = vi.hoisted(() => ({ user: { username: 'dad', is_minor: false } as { username: string; is_minor: boolean } }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../health/ScreenTimeControls', () => ({ default: () => <div data-testid="controls" /> }));
vi.mock('../health/SuggestionLanesControls', () => ({ default: () => null }));
vi.mock('../bible/ShareToChatSheet', () => ({
  default: ({ buildMessage }: { buildMessage: (n: string) => string }) => <div data-testid="share-sheet">{buildMessage('')}</div>,
}));
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getFamily: vi.fn(),
      getGuardianRecap: vi.fn(),
      getGuardianGuidance: vi.fn(),
      getGuardianJournal: vi.fn(),
      addGuardianJournal: vi.fn(),
      getGuardianActivity: vi.fn(),
      getMyParents: vi.fn(),
      askToTalk: vi.fn(),
      getGuardRequests: vi.fn(),
      decideGuardRequest: vi.fn(),
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
  concerns: [{ category: 'bullying', label: 'Bullying', level: 'high', count: 2, latest: { at: 'x', excerpt: 'nobody likes you', feature: 'sms' } }],
  highlights: ['Read the Bible on 2 days'],
};

const wrap = (ui: ReactElement, path = '/guardian') =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );

describe('Guardian page (a parent)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { username: 'dad', is_minor: false };
    vi.mocked(api.getFamily).mockResolvedValue([
      { username: 'dad', display_name: 'Dad', is_minor: false } as never,
      { username: 'emma', display_name: 'Emma', is_minor: true } as never,
      { username: 'sam', display_name: 'Sam', is_minor: true } as never,
    ]);
    vi.mocked(api.getGuardianRecap).mockResolvedValue({ recap });
    vi.mocked(api.getGuardianGuidance).mockResolvedValue({
      week: recap.week,
      prompts: [{ signal: 'bullying', title: 'Unkind words', ref: 'Psalm 139:14', verse: 'I will praise thee', prayer: 'Pray for Emma.', starter: 'Has anyone said anything to you lately that hurt?' }],
    });
    vi.mocked(api.getGuardianJournal).mockResolvedValue({ notes: [] });
    vi.mocked(api.addGuardianJournal).mockResolvedValue({} as never);
    vi.mocked(api.getGuardianActivity).mockResolvedValue({ events: [{ user: 'emma', feature: 'sms', item: 'Unknown', action: 'incoming', at: '2026-10-21T10:00:00Z', detail: { body: 'nobody likes you' } }] });
    vi.mocked(api.getGuardRequests).mockResolvedValue({ requests: [] });
    vi.mocked(api.decideGuardRequest).mockResolvedValue({} as never);
  });

  it('shows only this child\'s requests and answers them', async () => {
    const base = { label: '', note: '', status: 'pending' as const, until: null, decided_by: null, created_at: 'x', decided_at: null };
    vi.mocked(api.getGuardRequests).mockResolvedValue({
      requests: [
        { ...base, id: 1, username: 'sam', kind: 'app', target: 'com.roblox.client' },
        { ...base, id: 2, username: 'sam', kind: 'permission', target: 'com.snapchat.android|camera', label: 'Snapchat' },
        { ...base, id: 3, username: 'sam', kind: 'site', target: 'minecraft.net', note: 'school project' },
        { ...base, id: 4, username: 'emma', kind: 'site', target: 'tiktok.com' },
      ],
    });
    wrap(<Guardian />, '/guardian?child=sam');
    const card = await screen.findByTestId('guard-requests');
    expect(card).toHaveTextContent('Installed Roblox');
    expect(card).toHaveTextContent('Snapchat wants the camera');
    expect(card).toHaveTextContent('Asks to open minecraft.net: “school project”');
    expect(card).not.toHaveTextContent('tiktok.com');

    fireEvent.click(screen.getByRole('button', { name: 'Approve app' }));
    await waitFor(() => expect(api.decideGuardRequest).toHaveBeenCalledWith(1, { approve: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Allow 1 hour' }));
    await waitFor(() => expect(api.decideGuardRequest).toHaveBeenCalledWith(3, { approve: true, minutes: 60 }));
  });

  it('shows the asked-for child\'s week, concerns, guidance, activity and controls', async () => {
    wrap(<Guardian />, '/guardian?child=sam');
    await waitFor(() => expect(api.getGuardianRecap).toHaveBeenCalledWith('sam'));
    expect(await screen.findByTestId('guardian-concerns')).toHaveTextContent('nobody likes you');
    expect(screen.getByTestId('guardian-screen')).toHaveTextContent('2 h 30 min');
    expect(screen.getByText(/tiktok.com \(blocked 3×\)/)).toBeInTheDocument();
    expect(await screen.findByTestId('guidance-bullying')).toHaveTextContent('Psalm 139:14');
    expect(await screen.findByText('Unknown')).toBeInTheDocument();
    expect(screen.getByTestId('controls')).toBeInTheDocument();
  });

  it('turns a starter into a note the parent edits and sends themselves', async () => {
    wrap(<Guardian />);
    fireEvent.click(await screen.findByRole('button', { name: /Write Emma a note/ }));
    const box = screen.getByLabelText(/Your note to Emma/);
    fireEvent.change(box, { target: { value: 'Love you. Can we talk tonight?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send in chat…' }));
    expect(screen.getByTestId('share-sheet')).toHaveTextContent('Love you. Can we talk tonight?');
  });

  it('saves a journal note, shared if asked', async () => {
    wrap(<Guardian />);
    fireEvent.change(await screen.findByLabelText('New journal note'), { target: { value: 'Talked on the drive home.' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.addGuardianJournal).toHaveBeenCalledWith('emma', 'Talked on the drive home.', true));
  });

  it('explains itself to someone who is not a parent', async () => {
    vi.mocked(api.getFamily).mockRejectedValue(Object.assign(new Error('no'), { isAxiosError: true, response: { status: 403 } }));
    wrap(<Guardian />);
    expect(await screen.findByTestId('guardian-not-a-parent')).toBeInTheDocument();
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
    wrap(<TalkRequestCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'dad' })); // unpick dad
    fireEvent.change(screen.getByLabelText('A short note'), { target: { value: 'after dinner' } });
    fireEvent.click(screen.getByRole('button', { name: 'Let them know' }));
    await waitFor(() => expect(api.askToTalk).toHaveBeenCalledWith(['mom'], 'after dinner'));
    expect(await screen.findByTestId('talk-request-sent')).toHaveTextContent('mom will find a quiet moment');
  });

  it('is not shown to an adult', () => {
    auth.user = { username: 'dad', is_minor: false };
    const { container } = wrap(<TalkRequestCard />);
    expect(container).toBeEmptyDOMElement();
  });
});
