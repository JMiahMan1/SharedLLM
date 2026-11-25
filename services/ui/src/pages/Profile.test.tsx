import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import Profile from './Profile';
import { api } from '../services/api';
import type { ProfileStats } from '../lib/profileStats';

vi.mock('../context/AuthContext', async (importActual) => ({
  ...(await importActual<typeof import('../context/AuthContext')>()),
  useAuth: () => ({ user: { username: 'pat', is_admin: false, family_role: 'father' }, token: 'test' }),
}));

vi.mock('../services/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  api: {
    getProfileStats: vi.fn(),
    setProfileSharing: vi.fn(),
    getFamilyDirectory: vi.fn(),
    getAvatarImage: vi.fn(),
  },
}));

const MINE: ProfileStats = {
  username: 'pat', own: true, person: { username: 'pat', display_name: 'Pat' },
  cards: [
    { area: 'bible', state: 'ok', data: { streak_current: 4, streak_longest: 9, days_read: 30, chapters_read: 52, books_read: 2,
      reference: 'John 3', window: [{ day: '2026-11-23', count: 2 }], achievements: ['First Chapter'], points: 10,
      quiz_today: { correct: 6, total: 10 } } },
    { area: 'activity', state: 'ok', data: { steps_today: 4000, steps_average: 6500, workout_count: 2, drive_distance_miles: 40 } },
    { area: 'health', state: 'unavailable', data: null, reason: 'This part of Jarvis is not answering.' },
    { area: 'garden', state: 'empty', data: { streak_current: 0, streak_longest: 0, care_logs: 0, achievements: [], points: 0 } },
    { area: 'recipes', state: 'ok', data: { times_cooked: 3, recipes_cooked: 1, ratings_given: 1,
      most_made: [{ id: 'cookbook/chili.md', title: 'Chili', made_by_me: 3, mine: 5, rating: 4.5 }], favourites: [] } },
    { area: 'games', state: 'ok', data: { best_score: 1200, crowns: 1, podiums: 2, total_games: 3, bible_quiz_today: { correct: 6, total: 10 },
      games: [{ slug: 'family-checkers', title: 'Checkers', collection: 'family', best: 1200, rank: 1, plays: 4 }] } },
    { area: 'jarvis_use', state: 'ok', data: { days: 30, total: 12, top: [{ label: 'Music, video and podcasts', count: 7 }] } },
  ],
  sharing: {
    areas: ['recipes'], available: ['garden', 'recipes', 'jarvis_use'],
    activity_sharing: { enabled: true, audience: 'circle', share: ['totals', 'bible'] },
    health_shares: [{ viewer: 'robin', scope: 'sleep' }],
  },
};

const ROBIN: ProfileStats = {
  username: 'robin', own: false, person: { username: 'robin', display_name: 'Robin Lee' },
  cards: [
    { area: 'bible', state: 'ok', data: { streak_current: 3, streak_longest: 3, days_read: 12, chapters_read: 20, books_read: 1,
      achievements: [], points: 5, quiz_today: { correct: 8, total: 10 } } },
    { area: 'activity', state: 'hidden', data: null },
    { area: 'health', state: 'hidden', data: null },
    { area: 'garden', state: 'hidden', data: null },
    { area: 'recipes', state: 'hidden', data: null },
    { area: 'games', state: 'ok', data: { best_score: 900, crowns: 0, podiums: 1, total_games: 2, bible_quiz_today: { correct: 8, total: 10 },
      games: [{ slug: 'family-checkers', title: 'Checkers', collection: 'family', best: 900, rank: 2, plays: 2 }] } },
    { area: 'jarvis_use', state: 'hidden', data: null },
  ],
};

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname}{loc.search}</span>;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/profile/:username?" element={<Profile />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.getProfileStats).mockReset();
  vi.mocked(api.getProfileStats).mockImplementation(async (u: string) => (u === 'me' ? MINE : ROBIN));
  vi.mocked(api.getFamilyDirectory).mockResolvedValue([
    { username: 'robin', display_name: 'Robin Lee', nextcloud_user: null, avatar: null },
    { username: 'pat', display_name: 'Pat', nextcloud_user: null, avatar: null },
    { username: 'default', display_name: 'Household', nextcloud_user: null, avatar: null },
  ]);
  vi.mocked(api.setProfileSharing).mockResolvedValue({ ...MINE.sharing!, areas: ['garden', 'recipes'] });
});

describe('Profile page', () => {
  it('shows every area of your own, with a broken source greyed on its own card', async () => {
    renderAt('/profile');
    expect(await screen.findByText('My profile')).toBeTruthy();
    expect(api.getProfileStats).toHaveBeenCalledWith('me');
    expect(await screen.findByTestId('profile-card-bible')).toBeTruthy();
    expect(screen.getByTestId('profile-card-health-error').textContent).toMatch(/not answering/);
    expect(screen.getByTestId('profile-card-garden-empty')).toBeTruthy();
    // The household account is not a person.
    expect(screen.queryByTestId('profile-person-default')).toBeNull();
    expect(screen.getByTestId('profile-person-pat').textContent).toContain('Me');
  });

  it('makes every stat a link into the page that does that thing', async () => {
    renderAt('/profile');
    const streak = await screen.findByTestId('profile-bible-streak');
    expect(streak.getAttribute('href')).toBe('/bible?tab=read&ref=John%203');
    expect(screen.getByTestId('profile-recipe-cookbook/chili.md').getAttribute('href')).toBe('/recipes?recipe=cookbook%2Fchili.md');
    expect(screen.getByTestId('profile-game-family-checkers').getAttribute('href')).toBe('/family?tab=games&play=family-checkers');
    expect(screen.getByTestId('profile-bible-quiz').getAttribute('href')).toBe('/bible?tab=play');
    await userEvent.click(streak);
    expect(screen.getByTestId('where').textContent).toBe('/bible?tab=read&ref=John%203');
  });

  it("turns someone else's quiz score into a challenge to beat it", async () => {
    renderAt('/profile/robin');
    expect(await screen.findByText("Robin Lee's profile")).toBeTruthy();
    const quiz = screen.getByTestId('profile-bible-quiz');
    expect(quiz.getAttribute('href')).toBe('/bible?tab=play&beat=Robin&score=8&of=10');
    expect(quiz.textContent).toContain("Beat Robin's score");
    expect(screen.getByTestId('profile-game-family-checkers').getAttribute('aria-label')).toMatch(/Robin's best is 900/);
  });

  it('shows only what the person shares, and names the rest as private', async () => {
    renderAt('/profile/robin');
    await screen.findByTestId('profile-card-bible');
    expect(screen.queryByTestId('profile-card-garden')).toBeNull();
    expect(screen.queryByTestId('profile-sharing')).toBeNull();
    expect(screen.getByTestId('profile-private-areas').textContent).toMatch(/keeps activity and wander, health, garden, recipes, using jarvis private/);
  });

  it('says so when someone shares nothing at all', async () => {
    vi.mocked(api.getProfileStats).mockResolvedValue({ ...ROBIN, cards: ROBIN.cards.map((c) => ({ ...c, state: 'hidden', data: null })) });
    renderAt('/profile/robin');
    expect((await screen.findByTestId('profile-nothing-shared')).textContent).toMatch(/Robin has not shared/);
  });

  it('lets you share an area with a switch, and shows the existing switches instead of adding new ones', async () => {
    renderAt('/profile');
    const panel = await screen.findByTestId('profile-sharing');
    expect(within(panel).getByTestId('profile-share-recipes').getAttribute('aria-checked')).toBe('true');
    expect(within(panel).getByTestId('profile-share-activity').getAttribute('href')).toBe('/settings#settings-family');
    expect(within(panel).getByTestId('profile-share-activity').textContent).toMatch(/steps and distance, Bible reading/);
    expect(within(panel).getByTestId('profile-share-health').getAttribute('href')).toBe('/fitness?tab=health');
    // No switch for health or Bible here: one switch per area.
    expect(within(panel).queryByTestId('profile-share-bible')).toBeNull();
    await userEvent.click(within(panel).getByTestId('profile-share-garden'));
    await waitFor(() => expect(api.setProfileSharing).toHaveBeenCalledWith(['garden', 'recipes']));
  });

  it('says when no one has that name', async () => {
    vi.mocked(api.getProfileStats).mockRejectedValue(Object.assign(new Error('nf'), { isAxiosError: true, response: { status: 404 } }));
    renderAt('/profile/nobody');
    expect(await screen.findByTestId('profile-not-found')).toBeTruthy();
  });
});
