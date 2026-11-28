import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import ConcernsCard from '../components/member/ConcernsCard';
import { api } from '../services/api';
import type { GuardianConcern } from '../types/api';

const auth = vi.hoisted(() => ({
  user: { username: 'parent1', is_minor: false, family_role: 'father' } as {
    username: string;
    is_minor: boolean;
    is_admin?: boolean;
    family_role?: string;
  },
}));
vi.mock('../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getScreenTimeStatus: vi.fn(),
      setScreenRules: vi.fn(),
      getMemberDevices: vi.fn(),
      getGuardianConcerns: vi.fn(),
      getGuardianGuidance: vi.fn(),
    },
  };
});

/** A picture that arrived in a chat, with the words that came with it. */
const PICTURE: GuardianConcern = {
  id: 'm1',
  category: 'violence',
  label: 'Possible fight',
  level: 'high',
  excerpt: 'Bruise on an arm',
  feature: 'media',
  action: 'media_scan',
  item: 'chat-photo.jpg',
  at: '2026-11-26T16:20:00-07:00',
  message_context: {
    source: 'notification',
    direction: 'in',
    contact: 'Ava',
    app: 'WhatsApp',
    package: 'com.whatsapp',
    conversation: 'Ava',
    message_text: 'you have to see what he did',
    sent_at: 1_764_000_000_000,
  },
};

/** A voice note the child sent, transcribed. */
const VOICE: GuardianConcern = {
  id: 'a1',
  category: 'bullying',
  label: 'Possible bullying',
  level: 'high',
  excerpt: 'Meet me behind the gym after school or you will regret it',
  feature: 'sms',
  action: 'voice_message',
  item: 'Leo',
  at: '2026-11-26T16:40:00-07:00',
  message_context: {
    source: 'mms',
    direction: 'out',
    contact: 'Leo',
    number: '+15555550102',
    message_text: 'listen to this',
  },
};

const wrap = (ui: ReactElement) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { username: 'parent1', is_minor: false, family_role: 'father' };
  vi.mocked(api.getScreenTimeStatus).mockResolvedValue({
    username: 'kid1',
    day: '2026-11-26',
    can_manage: true,
    technical: true,
    comms: true,
    rules: { photo_scan: true, daily_limit_min: 120 },
    video_calls: {},
  } as never);
  vi.mocked(api.getMemberDevices).mockResolvedValue({ username: 'kid1', devices: [], tamper: [], offline_code: {} } as never);
  vi.mocked(api.getGuardianGuidance).mockResolvedValue({ prompts: [] } as never);
});

describe('a concern that arrived in a message', () => {
  it('says who sent the picture, through which app, and what came with it', async () => {
    vi.mocked(api.getGuardianConcerns).mockResolvedValue({ concerns: [PICTURE] });
    wrap(<ConcernsCard username="kid1" name="Kid One" />);

    const block = await screen.findByTestId('concern-message-context');
    expect(block).toHaveTextContent('From Ava');
    expect(block).toHaveTextContent('WhatsApp · Ava');
    expect(block).toHaveTextContent('you have to see what he did');
    // The picture is still described the way it was before.
    expect(screen.getByTestId('concern')).toHaveTextContent('In a photo or video from chat-photo.jpg');
  });

  it('turns a transcribed voice note into a message a parent can read', async () => {
    vi.mocked(api.getGuardianConcerns).mockResolvedValue({ concerns: [VOICE] });
    wrap(<ConcernsCard username="kid1" name="Kid One" />);

    expect(await screen.findByTestId('concern-message-context')).toHaveTextContent('Sent to Leo');
    const concern = screen.getByTestId('concern');
    expect(concern).toHaveTextContent('In a voice message');
    expect(concern).toHaveTextContent('Meet me behind the gym after school');
    // "from Leo" would read as though Leo sent it; the badge already says who.
    expect(concern).toHaveTextContent('In a voice message:');
  });

  it('keeps a concern with no message context exactly as it was', async () => {
    vi.mocked(api.getGuardianConcerns).mockResolvedValue({
      concerns: [{ ...PICTURE, id: 'g1', message_context: null, item: 'IMG_0042.jpg', action: 'gallery_scan' }],
    });
    wrap(<ConcernsCard username="kid1" name="Kid One" />);

    await screen.findByTestId('concern');
    expect(screen.queryByTestId('concern-message-context')).not.toBeInTheDocument();
  });
});
