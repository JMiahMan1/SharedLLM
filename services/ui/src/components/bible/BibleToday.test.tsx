import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleToday from './BibleToday';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'sam' } }) }));

const apiMock = vi.hoisted(() => ({
  getBibleDaily: vi.fn(),
  getBiblePlans: vi.fn(),
  getBibleVerseContext: vi.fn(),
  getBibleDevotionalWorks: vi.fn(),
  listBiblePlanGroups: vi.fn(),
  getBibleWelcomeBackVerse: vi.fn(),
  getBibleStreakMilestoneVerse: vi.fn(),
  getBibleDailyEncouragement: vi.fn(),
  useBibleGraceDay: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

beforeEach(() => {
  apiMock.getBibleDevotionalWorks.mockResolvedValue({ works: [] });
  apiMock.listBiblePlanGroups.mockResolvedValue({ groups: [] });
});

describe('Today tab', () => {
  it('offers to continue where the reader left off, and sets the reminder', async () => {
    const user = userEvent.setup();
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-13',
      verse_of_day: { reference: 'Psalm 23:1', text: 'The LORD is my shepherd.' },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
      position: { book: 'John', chapter: 3, verse: 1, reference: 'John 3' },
    });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    const onOpenRef = vi.fn();
    const onReminderTime = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={onOpenRef} reminderTime="" onReminderTime={onReminderTime} />
      </QueryClientProvider>,
    );
    await user.click(await screen.findByTestId('bible-continue'));
    expect(onOpenRef).toHaveBeenCalledWith('John 3');
    expect(screen.getByTestId('bible-reminder')).toHaveTextContent('Off');
  });

  it('opens verse options on right-click and fetches the context note', async () => {
    const user = userEvent.setup();
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-17',
      verse_of_day: {
        day: '2026-10-17',
        version: 'kjv',
        scope: 'all',
        reference: 'Psalm 23:1',
        text: 'The LORD is my shepherd.',
      },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
    });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    apiMock.getBibleVerseContext.mockResolvedValue({
      reference: 'Psalm 23:1',
      body: 'David, a shepherd, describes the LORD in the terms of his own work.',
      verses_used: 9,
      notes_used: 0,
    });
    const onOpenRef = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={onOpenRef} />
      </QueryClientProvider>,
    );

    const verse = await screen.findByTestId('bible-votd');
    fireEvent.contextMenu(verse, { clientX: 40, clientY: 50 });
    expect(screen.getByTestId('bible-votd-menu')).toBeInTheDocument();

    await user.click(screen.getByTestId('bible-votd-context'));
    expect(await screen.findByTestId('bible-context-body')).toHaveTextContent(
      'David, a shepherd, describes the LORD',
    );
    expect(onOpenRef).not.toHaveBeenCalled();
    expect(apiMock.getBibleVerseContext).toHaveBeenCalledWith(
      expect.objectContaining({ version: 'kjv', scope: 'all' }),
    );
  });

  it('suppresses the passage tap after a press-and-hold opens the menu', async () => {
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-17',
      verse_of_day: { reference: 'Psalm 23:1', text: 'The LORD is my shepherd.' },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
    });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    const onOpenRef = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={onOpenRef} />
      </QueryClientProvider>,
    );

    const verse = await screen.findByTestId('bible-votd');
    vi.useFakeTimers();
    try {
      fireEvent.pointerDown(verse, { pointerType: 'touch', clientX: 20, clientY: 20 });
      act(() => {
        vi.advanceTimersByTime(500);
      });
      expect(screen.getByTestId('bible-votd-menu')).toBeInTheDocument();

      fireEvent.pointerUp(verse);
      fireEvent.click(verse);
      expect(onOpenRef).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('still opens the passage on a normal tap', async () => {
    const user = userEvent.setup();
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-17',
      verse_of_day: { reference: 'Psalm 23:1', text: 'The LORD is my shepherd.' },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
    });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    const onOpenRef = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={onOpenRef} />
      </QueryClientProvider>,
    );

    await user.click(await screen.findByTestId('bible-votd'));
    expect(onOpenRef).toHaveBeenCalledWith('Psalm 23:1');
  });

  it('keeps the saved devotional selected on Today', async () => {
    apiMock.getBibleDevotionalWorks.mockResolvedValue({
      works: [{ id: 2, title: 'Devotions for a Sacred Marriage', author: '', kind: 'devotional', chapter_count: 52 }],
    });
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-21',
      verse_of_day: { reference: 'Psalm 23:1', text: 'The LORD is my shepherd.' },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
    });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    apiMock.getBibleVerseContext.mockResolvedValue({ note: null });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={vi.fn()} devotionalWork="book:2" />
      </QueryClientProvider>,
    );

    const select = await screen.findByTestId('bible-devotional-work');
    expect(select).toHaveValue('book:2');
    await vi.waitFor(() => expect(apiMock.getBibleDaily).toHaveBeenCalledWith('book:2'));
  });

  it('remembers a devotional chosen from the selector', async () => {
    const user = userEvent.setup();
    apiMock.getBibleDevotionalWorks.mockResolvedValue({
      works: [{ id: 2, title: 'Devotions for a Sacred Marriage', author: '', kind: 'devotional', chapter_count: 52 }],
    });
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-21',
      verse_of_day: { reference: 'Psalm 23:1', text: 'The LORD is my shepherd.' },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
    });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    apiMock.getBibleVerseContext.mockResolvedValue({ note: null });
    const onDevotionalWork = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={vi.fn()} onDevotionalWork={onDevotionalWork} />
      </QueryClientProvider>,
    );

    const select = await screen.findByTestId('bible-devotional-work');
    await user.selectOptions(select, 'book:2');
    expect(onDevotionalWork).toHaveBeenCalledWith('book:2');
  });

  it('shows welcome-back card when returning after a break', async () => {
    apiMock.getBibleDevotionalWorks.mockResolvedValue({ works: [] });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-21',
      verse_of_day: { reference: 'Psalm 23:1', text: 'The LORD is my shepherd.' },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
      streaks: { read_streak_current: 0, days_read: 5, chapters_read: 10 },
    });
    apiMock.getBibleWelcomeBackVerse.mockResolvedValue({
      reference: 'Lamentations 3:22-23',
      text: 'The steadfast love of the LORD never ceases; his mercies never come to an end; they are new every morning; great is your faithfulness.',
      book: 'Lamentations',
      chapter: 3,
      verse: 22,
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findByTestId('bible-welcome-back')).toBeInTheDocument();
    expect(screen.getByText('Welcome back')).toBeInTheDocument();
    expect(screen.getByText(/You've read 5 days before/)).toBeInTheDocument();
    expect(screen.getByText('Lamentations 3:22-23')).toBeInTheDocument();
  });

  it('shows milestone celebration card for streak milestones', async () => {
    apiMock.getBibleDevotionalWorks.mockResolvedValue({ works: [] });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-21',
      verse_of_day: { reference: 'Psalm 23:1', text: 'The LORD is my shepherd.' },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
      streaks: { read_streak_current: 7, days_read: 20, chapters_read: 10 },
    });
    apiMock.getBibleStreakMilestoneVerse.mockResolvedValue({
      reference: 'Psalm 119:164',
      text: 'Seven times a day I praise you for your righteous rules.',
      book: 'Psalm',
      chapter: 119,
      verse: 164,
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findByTestId('bible-streak-milestone')).toBeInTheDocument();
    expect(screen.getByText('7 days!')).toBeInTheDocument();
    expect(screen.getByText('A full week of faithfulness.')).toBeInTheDocument();
    expect(screen.getByText('Psalm 119:164')).toBeInTheDocument();
  });

  it('shows grace day offer when streak > 0 but no reading today', async () => {
    apiMock.getBibleDevotionalWorks.mockResolvedValue({ works: [] });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-21',
      verse_of_day: { reference: 'Psalm 23:1', text: 'The LORD is my shepherd.' },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
      streaks: { read_streak_current: 5, days_read: 20, chapters_read: 10, grace_day_used: false },
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(await screen.findByTestId('bible-grace-day')).toBeInTheDocument();
    expect(screen.getByText('Grace day available')).toBeInTheDocument();
    expect(screen.getByText('Use grace day')).toBeInTheDocument();
  });

  it('does not show grace day when already used', async () => {
    apiMock.getBibleDevotionalWorks.mockResolvedValue({ works: [] });
    apiMock.getBiblePlans.mockResolvedValue({ catalogue: [], mine: [] });
    apiMock.getBibleDaily.mockResolvedValue({
      day: '2026-10-21',
      verse_of_day: { reference: 'Psalm 23:1', text: 'The LORD is my shepherd.' },
      devotional: { entry: null, skipped: [], reason: 'none' },
      sources: [],
      streaks: { read_streak_current: 5, days_read: 20, chapters_read: 10, grace_day_used: true },
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BibleToday onOpenRef={vi.fn()} />
      </QueryClientProvider>,
    );

    expect(screen.queryByTestId('bible-grace-day')).not.toBeInTheDocument();
  });
});
