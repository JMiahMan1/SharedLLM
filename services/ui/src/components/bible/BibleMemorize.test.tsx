import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleMemorize from './BibleMemorize';
import type { BibleMemoryOverview, BibleMemoryVerse } from '../../types/api';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({
  getBibleMemory: vi.fn(),
  addBibleMemory: vi.fn(),
  removeBibleMemory: vi.fn(),
  getBibleMemoryDue: vi.fn(),
  recallBibleMemory: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

const today = new Date().toISOString().slice(0, 10);
const VERSE: BibleMemoryVerse = {
  id: 7, ref: 'John 11:35', set_id: '', version: 'kjv', level: 1, stage: 'cloze', mastered: false,
  due_on: today, due: true, reviews: 1, lapses: 0, last_score: 0,
};
const OVERVIEW: BibleMemoryOverview = {
  verses: [VERSE],
  due_count: 1,
  mastered: 0,
  sets: [{ id: 'kids', name: "Kids' First Verses", blurb: 'Short verses', count: 10, added: 0 }],
};

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BibleMemorize page="light" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getBibleMemory.mockResolvedValue(OVERVIEW);
});

describe('Bible memorisation', () => {
  it('lists verses with their stage and offers the due practice', async () => {
    renderIt();
    expect(await screen.findByText('John 11:35')).toBeInTheDocument();
    expect(screen.getByText('Fill the gaps')).toBeInTheDocument();
    expect(screen.getByTestId('bible-memory-practice')).toHaveTextContent('Practise 1 due verse');
  });

  it('adds a whole set', async () => {
    const user = userEvent.setup();
    apiMock.addBibleMemory.mockResolvedValue({ ...OVERVIEW, added: ['Genesis 1:1', 'Psalm 118:24'], skipped: [] });
    renderIt();
    await user.click(await screen.findByTestId('bible-memory-set-kids'));
    expect(apiMock.addBibleMemory).toHaveBeenCalledWith({ set_id: 'kids' });
    expect(await screen.findByRole('status')).toHaveTextContent('Added 2 verses');
  });

  it('fills the gaps, sends the whole verse, and shows what slipped', async () => {
    const user = userEvent.setup();
    apiMock.getBibleMemoryDue.mockResolvedValue({
      count: 1,
      due: [{ ...VERSE, text: 'Jesus wept.', cloze: [{ word: 'Jesus', hidden: false }, { word: 'wept', hidden: true }], first_letters: 'J____ w___' }],
    });
    apiMock.recallBibleMemory.mockResolvedValue({
      score: 1, passed: true, text: 'Jesus wept.',
      words: [{ word: 'Jesus', ok: true }, { word: 'wept', ok: true }],
      verse: { ...VERSE, level: 2, stage: 'first_letters', due_on: '2099-01-01', due: false },
      level_before: 1, newly_earned: [],
    });
    renderIt();
    await user.click(await screen.findByTestId('bible-memory-practice'));
    await user.type(await screen.findByLabelText('Missing word 2'), 'wept');
    await user.click(screen.getByTestId('bible-recall-check'));
    await waitFor(() => expect(apiMock.recallBibleMemory).toHaveBeenCalledWith(7, 'Jesus wept'));
    expect(await screen.findByTestId('bible-recall-verdict')).toHaveTextContent('Got it!');
    await user.click(screen.getByRole('button', { name: /Next/ }));
    expect(await screen.findByTestId('bible-memory-done')).toHaveTextContent('1 of 1 recalled');
  });
});
