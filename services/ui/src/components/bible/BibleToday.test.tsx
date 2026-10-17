import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleToday from './BibleToday';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({
  getBibleDaily: vi.fn(),
  getBiblePlans: vi.fn(),
  getBibleVerseContext: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => ({
  api: { ...(await importOriginal<typeof import('../../services/api')>()).api, ...apiMock },
}));

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
});
