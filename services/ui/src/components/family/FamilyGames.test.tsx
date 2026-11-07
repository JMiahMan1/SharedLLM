import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FamilyGames from './FamilyGames';
import { api } from '../../services/api';
import type { FamilyGameCard, FamilyGamesResponse } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getFamilyGames: vi.fn(),
      setFamilyGameState: vi.fn(),
      rateArcadeGame: vi.fn(),
      getArcadeScoreboard: vi.fn(),
      getArcadeHighscores: vi.fn(),
      submitArcadeScore: vi.fn(),
      getArcadeTvs: vi.fn(),
      castArcadeTv: vi.fn(),
      getPartyRooms: vi.fn(),
      endPartyRoom: vi.fn(),
    },
  };
});

const m = {
  getFamilyGames: vi.mocked(api.getFamilyGames),
  setState: vi.mocked(api.setFamilyGameState),
  rate: vi.mocked(api.rateArcadeGame),
  scoreboard: vi.mocked(api.getArcadeScoreboard),
  highscores: vi.mocked(api.getArcadeHighscores),
  submit: vi.mocked(api.submitArcadeScore),
  getTvs: vi.mocked(api.getArcadeTvs),
  castTv: vi.mocked(api.castArcadeTv),
  partyRooms: vi.mocked(api.getPartyRooms),
  endRoom: vi.mocked(api.endPartyRoom),
};

function card(slug: string, overrides: Partial<FamilyGameCard> = {}): FamilyGameCard {
  return {
    slug,
    title: slug.replace('family-', '').replace(/-/g, ' '),
    collection: 'family',
    family_state: 'published',
    source: 'built-in',
    emoji: '☁️',
    tagline: 'Hop to the stars',
    art: { from: '#123456', to: '#654321' },
    rating: { count: 4, average: 4.5 },
    plays: 3,
    on_shelf: true,
    shelf_reason: '',
    ...overrides,
  };
}

const base: FamilyGamesResponse = {
  success: true,
  arcade_available: true,
  play_base: 'https://games.sumemail.com',
  is_admin: false,
  games: [card('family-cloud-hop')],
  review: [],
  unpublished: [],
};

function renderIt() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <FamilyGames />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  m.partyRooms.mockResolvedValue({ success: true, rooms: [] });
  m.scoreboard.mockResolvedValue({
    success: true,
    arcade_available: true,
    players: [
      {
        username: 'kate',
        display_name: 'Kate',
        initials: 'KAT',
        player_url: 'https://games.sumemail.com/player/KAT',
        you: true,
        best_score: 0,
        total_games: 1,
        games_scored: 0,
        submits: 0,
        podiums: 0,
        crowns: 0,
      },
    ],
  });
  m.highscores.mockResolvedValue({ success: true, arcade_available: true, players: [], games: [] });
});

describe('FamilyGames', () => {
  it('shows players the shelf with no admin controls', async () => {
    m.getFamilyGames.mockResolvedValue(base);
    renderIt();
    expect(await screen.findByTestId('family-game-family-cloud-hop')).toBeInTheDocument();
    expect(screen.queryByTestId('family-admin')).not.toBeInTheDocument();
    expect(screen.getByTestId('family-rating-family-cloud-hop')).toHaveTextContent('4.5 · 4 votes');
    expect(m.scoreboard).toHaveBeenCalledWith('family');
    expect(m.highscores).toHaveBeenCalledWith('family');
  });

  it('tells players when nothing has made the shelf yet', async () => {
    m.getFamilyGames.mockResolvedValue({ ...base, games: [] });
    renderIt();
    expect(await screen.findByTestId('family-games-empty')).toHaveTextContent('being tested');
  });

  it('renders an honest offline card', async () => {
    m.getFamilyGames.mockResolvedValue({ ...base, arcade_available: false, error: 'Arcade unreachable: boom', games: [] });
    renderIt();
    expect(await screen.findByTestId('family-games-offline')).toHaveTextContent('Arcade unreachable: boom');
  });

  it('lets an admin test, see why a game is held back, and publish it', async () => {
    m.getFamilyGames.mockResolvedValue({
      ...base,
      is_admin: true,
      admin_token_configured: true,
      review: [card('family-petal-swap', { family_state: 'testing', on_shelf: false, shelf_reason: 'In testing' })],
      unpublished: [card('family-orbit-guard', { family_state: 'unpublished', on_shelf: false, shelf_reason: 'Unpublished' })],
    });
    m.setState.mockResolvedValue({ success: true, state: 'published' });
    renderIt();

    const row = await screen.findByTestId('family-admin-family-petal-swap');
    expect(within(row).getByTestId('family-reason-family-petal-swap')).toHaveTextContent('In testing');
    expect(within(row).getByRole('button', { name: /Test play/ })).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: /Publish/ }));
    await waitFor(() => expect(m.setState).toHaveBeenCalledWith('family-petal-swap', 'published'));

    const retired = screen.getByTestId('family-admin-family-orbit-guard');
    expect(within(retired).queryByRole('button', { name: /Test play/ })).not.toBeInTheDocument();
    fireEvent.click(within(retired).getByRole('button', { name: /Back to testing/ }));
    await waitFor(() => expect(m.setState).toHaveBeenCalledWith('family-orbit-guard', 'testing'));
  });

  it('warns the admin and disables publishing when the arcade token is missing', async () => {
    m.getFamilyGames.mockResolvedValue({
      ...base,
      is_admin: true,
      admin_token_configured: false,
      review: [card('family-petal-swap', { family_state: 'testing', on_shelf: false, shelf_reason: 'In testing' })],
    });
    renderIt();
    expect(await screen.findByTestId('family-token-missing')).toHaveTextContent('ARCADE_ADMIN_TOKEN');
    const row = screen.getByTestId('family-admin-family-petal-swap');
    expect(within(row).getByRole('button', { name: /Publish/ })).toBeDisabled();
  });

  it('votes with large tap targets', async () => {
    m.getFamilyGames.mockResolvedValue(base);
    m.rate.mockResolvedValue({ success: true, rating: { count: 5, average: 4.6 } });
    renderIt();
    const vote = await screen.findByTestId('family-vote-family-cloud-hop');
    const five = within(vote).getByRole('button', { name: /5 stars/ });
    expect(five.className).toContain('h-11');
    fireEvent.click(five);
    await waitFor(() => expect(m.rate).toHaveBeenCalledWith('family-cloud-hop', 5));
  });

  it('saves a finished run from the embedded game once, under the signed-in member', async () => {
    m.getFamilyGames.mockResolvedValue(base);
    m.submit.mockResolvedValue({ success: true, rank: 1 });
    renderIt();
    fireEvent.click(await screen.findByRole('button', { name: 'Play cloud hop' }));
    const frame = (await screen.findByTestId('family-player-frame')) as HTMLIFrameElement;
    const url = new URL(frame.src);
    expect(url.origin + url.pathname).toBe('https://games.sumemail.com/play/family-cloud-hop');
    // The Jarvis member plays as themselves; the game is told the insets.
    expect(url.searchParams.get('embed')).toBe('1');
    expect(url.searchParams.get('player')).toBe('KAT');
    expect(url.searchParams.get('name')).toBe('Kate');
    expect(url.searchParams.get('safe')).toMatch(/^\d+,\d+,\d+,\d+$/);

    const post = (data: unknown, origin = 'https://games.sumemail.com') =>
      window.dispatchEvent(new MessageEvent('message', { data, origin }));
    const run = { source: 'arcade-score', slug: 'family-cloud-hop', score: 420, capture_id: 'family-cloud-hop:r1' };
    post(run, 'https://evil.example');
    post(run);
    post(run);
    await waitFor(() => expect(m.submit).toHaveBeenCalledTimes(1));
    expect(m.submit).toHaveBeenCalledWith('family-cloud-hop', 420, 'family-cloud-hop:r1');
  });

  it('takes the insets the SystemBars plugin injects, where env() reads 0', async () => {
    // Android: Capacitor's SystemBars (insetsHandling: 'css') writes the real
    // values as CSS variables because env(safe-area-inset-*) is unreliable
    // there. jsdom never computes env(), so this is the Android case exactly.
    document.documentElement.style.setProperty('--safe-area-inset-top', '28px');
    document.documentElement.style.setProperty('--safe-area-inset-bottom', '34px');
    try {
      m.getFamilyGames.mockResolvedValue(base);
      renderIt();
      fireEvent.click(await screen.findByRole('button', { name: 'Play cloud hop' }));
      const frame = (await screen.findByTestId('family-player-frame')) as HTMLIFrameElement;
      const safe = new URL(frame.src).searchParams.get('safe')?.split(',').map(Number) ?? [];
      expect(safe).toHaveLength(4);
      expect(safe[0]).toBeGreaterThanOrEqual(28);
      expect(safe[2]).toBeGreaterThanOrEqual(34);
    } finally {
      document.documentElement.style.removeProperty('--safe-area-inset-top');
      document.documentElement.style.removeProperty('--safe-area-inset-bottom');
    }
  });

  it('opens edge to edge and closes from the game\'s own Exit', async () => {
    m.getFamilyGames.mockResolvedValue(base);
    renderIt();
    fireEvent.click(await screen.findByRole('button', { name: 'Play cloud hop' }));
    expect(await screen.findByTestId('family-player')).toBeInTheDocument();
    expect(screen.getByTestId('family-player-splash')).toBeInTheDocument();

    const relay = (data: unknown, origin = 'https://games.sumemail.com') =>
      window.dispatchEvent(new MessageEvent('message', { data, origin }));
    relay({ source: 'arcade-relay', slug: 'family-cloud-hop', type: 'ready' });
    await waitFor(() => expect(screen.getByTestId('family-player-splash')).toHaveAttribute('aria-hidden', 'true'));

    relay({ source: 'arcade-relay', slug: 'family-cloud-hop', type: 'exit' }, 'https://evil.example');
    expect(screen.getByTestId('family-player')).toBeInTheDocument();
    relay({ source: 'arcade-relay', slug: 'family-cloud-hop', type: 'exit' });
    await waitFor(() => expect(screen.queryByTestId('family-player')).not.toBeInTheDocument());
  });

  it('warms up the game host and the shelf games before Play is tapped', async () => {
    m.getFamilyGames.mockResolvedValue(base);
    const { unmount } = renderIt();
    await screen.findByTestId('family-game-family-cloud-hop');
    const hrefs = (rel: string) => [...document.head.querySelectorAll(`link[rel="${rel}"]`)].map((l) => l.getAttribute('href'));
    expect(hrefs('preconnect')).toContain('https://games.sumemail.com');
    expect(hrefs('prefetch')).toContain('https://games.sumemail.com/game/family-cloud-hop/index.html');
    unmount();
    expect(hrefs('prefetch')).not.toContain('https://games.sumemail.com/game/family-cloud-hop/index.html');
  });

  it('shows a TV Stage cast link on multiplayer party games', async () => {
    m.getFamilyGames.mockResolvedValue({
      ...base,
      games: [card('family-good-word', { title: 'Parable Party', party: true, tv_path: '/tv', ages: '7+', players: '1-8' })],
    });
    renderIt();
    const tvLink = await screen.findByTestId('family-tv-family-good-word');
    expect(tvLink).toHaveAttribute('href', 'https://games.sumemail.com/tv');
  });

  it('relays discovered screens and reports the real cast outcome back to the game', async () => {
    m.getFamilyGames.mockResolvedValue({
      ...base,
      games: [card('family-good-word', { title: 'Parable Party', party: true, tv_path: '/tv' })],
    });
    const screenRow = {
      id: 'ha:media_player.living_room_lg',
      name: 'Living Room LG',
      platform: 'webos',
      state: 'on',
      display: 'web' as const,
      methods: ['webos_browser'],
      method_labels: ['LG web browser (live page)'],
    };
    m.getTvs.mockResolvedValue({ success: true, tvs: [screenRow] });
    m.castTv.mockResolvedValue({ success: true, screen: screenRow, method: 'webos_browser', display: 'web', verified: true });
    renderIt();
    fireEvent.click(await screen.findByRole('button', { name: 'Play Parable Party' }));
    await screen.findByTestId('family-player');

    const relay = (data: unknown, origin = 'https://games.sumemail.com') =>
      window.dispatchEvent(new MessageEvent('message', { data, origin }));
    relay({ source: 'arcade-relay', slug: 'family-good-word', type: 'list-tvs' });
    await waitFor(() => expect(m.getTvs).toHaveBeenCalledWith(false));

    relay({
      source: 'arcade-relay',
      slug: 'family-good-word',
      type: 'cast',
      payload: { room_code: 'ZION', screen_id: 'ha:media_player.living_room_lg' },
    });
    await waitFor(() =>
      expect(m.castTv).toHaveBeenCalledWith({
        screen_id: 'ha:media_player.living_room_lg',
        room_code: 'ZION',
        method: undefined,
        stop: false,
      }),
    );
  });
});

describe('FamilyGames party rooms (admin)', () => {
  it('lists live party rooms and ends one after a second tap', async () => {
    m.getFamilyGames.mockResolvedValue({ ...base, is_admin: true, admin_token_configured: true });
    m.partyRooms.mockResolvedValue({
      success: true,
      rooms: [{ code: 'ZION', host_name: 'Jeremiah', phase: 'voting', round: 2, total_rounds: 7, player_count: 3, players: [], stage_connected: true }],
    });
    m.endRoom.mockResolvedValue({ success: true, ended: 'ZION' });
    renderIt();
    const row = await screen.findByTestId('party-room-ZION');
    expect(row).toHaveTextContent('Jeremiah + 2');
    expect(row).toHaveTextContent('Voting · Round 2/7 · On a TV');
    fireEvent.click(within(row).getByRole('button', { name: /End game/ }));
    expect(m.endRoom).not.toHaveBeenCalled();
    fireEvent.click(within(row).getByRole('button', { name: /Tap to confirm/ }));
    await waitFor(() => expect(m.endRoom).toHaveBeenCalledWith('ZION'));
  });

  it('does not ask non-admins for party rooms', async () => {
    m.getFamilyGames.mockResolvedValue({ ...base, is_admin: false });
    renderIt();
    await screen.findByTestId('family-shelf').catch(() => null);
    expect(m.partyRooms).not.toHaveBeenCalled();
  });
});
