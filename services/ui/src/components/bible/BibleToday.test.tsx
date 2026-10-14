import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BibleToday from './BibleToday';

vi.mock('../../hooks/useHaptics', () => ({ useHaptics: () => ({ trigger: vi.fn() }) }));

const apiMock = vi.hoisted(() => ({
  getBibleDaily: vi.fn(),
  getBiblePlans: vi.fn(),
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
});
