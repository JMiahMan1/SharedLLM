import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleGames from './BibleGames';
import type { BibleGame, BibleGameResult } from '../../types/api';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({
  getBibleGames: vi.fn(),
  getBibleGame: vi.fn(),
  submitBibleGame: vi.fn(),
  getBibleLeaderboard: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

const DAILY: BibleGame = {
  mode: 'daily',
  name: 'Daily Quiz',
  seed: '2026-10-13',
  version: 'kjv',
  seconds: null,
  played_today: null,
  questions: [
    { id: 0, kind: 'which_book', prompt: 'Which book is this verse from?', text: 'For God so loved the world…', options: ['John', 'Mark', 'Luke', 'Acts'] },
    { id: 1, kind: 'book_after', prompt: 'Which book comes right after Ruth?', options: ['1 Samuel', 'Judges', 'Ezra', 'Job'] },
  ],
};

const RESULT: BibleGameResult = {
  mode: 'daily',
  correct: 1,
  total: 2,
  duration_ms: 12000,
  counted: true,
  results: [
    { id: 0, correct: true, chosen: 'John', answer: 'John', reference: 'John 3:16', text: 'For God so loved the world…' },
    { id: 1, correct: false, chosen: 'Judges', answer: '1 Samuel', reference: 'Ruth, then 1 Samuel', text: '' },
  ],
  stars: { granted: 1, status: '1 star(s) banked' },
  newly_earned: [{ id: 'quiz_first', name: 'First Quiz', description: '', points: 5, earned_on: '2026-10-13' } as never],
};

function renderGames() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BibleGames page="sepia" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getBibleGames.mockResolvedValue({
    today: '2026-10-13',
    modes: [
      { mode: 'daily', name: 'Daily Quiz', blurb: 'Ten questions', count: 10, daily: true, seconds: null },
      { mode: 'verse_race', name: 'Verse Race', blurb: 'Sixty seconds', count: 15, daily: false, seconds: 60 },
      { mode: 'which_book', name: 'Which Book?', blurb: 'Pick the book', count: 8, daily: false, seconds: null },
    ],
  });
  apiMock.getBibleGame.mockResolvedValue(DAILY);
  apiMock.getBibleLeaderboard.mockResolvedValue({
    day: '2026-10-13',
    entries: [
      { username: 'michele', correct: 9, total: 10, duration_ms: 50000, you: false },
      { username: 'jeremiah', correct: 7, total: 10, duration_ms: 61000, you: true },
    ],
  });
  apiMock.submitBibleGame.mockResolvedValue(RESULT);
});

describe('Bible games', () => {
  it('shows the daily quiz, the other games and the family leaderboard', async () => {
    renderGames();
    expect(await screen.findByTestId('bible-game-start-daily')).toBeInTheDocument();
    expect(screen.getByTestId('bible-game-start-verse_race')).toHaveTextContent('60s');
    expect(await screen.findByText('jeremiah (you)')).toBeInTheDocument();
    expect(screen.getByText('michele')).toBeInTheDocument();
  });

  it('plays a round, sends only the choices, and reviews the answers', async () => {
    const user = userEvent.setup();
    renderGames();
    await user.click(await screen.findByTestId('bible-game-start-daily'));
    expect(await screen.findByTestId('bible-game-verse')).toHaveTextContent('For God so loved');
    await user.click(screen.getByRole('button', { name: 'John' }));
    expect(screen.getByTestId('bible-game-progress')).toHaveTextContent('2 / 2');
    await user.click(screen.getByRole('button', { name: 'Judges' }));

    await waitFor(() => expect(apiMock.submitBibleGame).toHaveBeenCalled());
    const [mode, body] = apiMock.submitBibleGame.mock.calls[0];
    expect(mode).toBe('daily');
    expect(body).toMatchObject({ seed: '2026-10-13', version: 'kjv', answers: ['John', 'Judges'] });

    expect(await screen.findByTestId('bible-game-score')).toHaveTextContent('1/2');
    expect(screen.getByText(/1 star banked/)).toBeInTheDocument();
    expect(screen.getByText(/New badge: First Quiz/)).toBeInTheDocument();
    expect(screen.getByText(/you chose Judges/)).toBeInTheDocument();
  });

  it('sets the verse on an opaque page in the reader’s tone', async () => {
    const user = userEvent.setup();
    renderGames();
    await user.click(await screen.findByTestId('bible-game-start-daily'));
    const verse = await screen.findByTestId('bible-game-verse');
    expect(verse.closest('section')?.style.backgroundColor).toMatch(/^(#f4ecd8|rgb\(244, 236, 216\))$/i);
  });

  it('hands in the race when the clock runs out', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      apiMock.getBibleGame.mockResolvedValue({ ...DAILY, mode: 'verse_race', name: 'Verse Race', seconds: 2 });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      renderGames();
      await user.click(await screen.findByTestId('bible-game-start-verse_race'));
      expect(await screen.findByTestId('bible-game-clock')).toBeInTheDocument();
      await act(async () => {
        vi.advanceTimersByTime(2500);
      });
      await waitFor(() => expect(apiMock.submitBibleGame).toHaveBeenCalled());
      expect(apiMock.submitBibleGame.mock.calls[0][1].answers).toEqual([null, null]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('names the reason when a game cannot be built', async () => {
    const user = userEvent.setup();
    apiMock.getBibleGame.mockImplementation(async (mode: string) => {
      if (mode === 'which_book') throw { response: { data: { detail: 'Install a complete translation in Admin > Bible.' } } };
      return DAILY;
    });
    renderGames();
    await user.click(await screen.findByTestId('bible-game-start-which_book'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Install a complete translation');
  });
});
