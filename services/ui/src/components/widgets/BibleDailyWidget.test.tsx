import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import BibleDailyWidget from './BibleDailyWidget';
import { api } from '../../services/api';
import type { BibleDailyResponse } from '../../types/api';
import type { UserWidgetSettings } from '../../types/widget';

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('../../hooks/useHaptics', () => ({
  useHaptics: () => ({ trigger: vi.fn(), isEnabled: () => true, setEnabled: vi.fn() }),
}));

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), getBibleDaily: vi.fn() },
  };
});

const verse = {
  day: '2026-10-03',
  day_of_year: 276,
  version: 'kjv',
  scope: 'all' as const,
  osis: 'John',
  book: 'John',
  book_name: 'John',
  chapter: 3,
  verse: 16,
  reference: 'John 3:16',
  text: 'For God so loved the world, that he gave his only begotten Son, that whosoever believeth in him should not perish.',
};

const daily = (overrides: Partial<BibleDailyResponse> = {}): BibleDailyResponse => ({
  day: '2026-10-03',
  verse_of_day: verse,
  devotional: {
    source: 'blb',
    entry: {
      source: 'blb',
      work: 'dbdbg',
      title: 'Day by Day by Grace',
      day_of_year: 276,
      kind: 'link',
      reference: '',
      text: '',
      url: 'https://bible.test/devotionals/dbdbg/view.cfm?doy=276',
    },
    skipped: [],
  },
  sources: [],
  streaks: {
    read_streak_current: 4,
    read_streak_longest: 11,
    open_streak_current: 6,
    days_read: 30,
    chapters_read: 122,
  },
  position: { book: 'John', chapter: 3, verse: 16 },
  ...overrides,
});

const settings = (overrides: Partial<UserWidgetSettings> = {}): UserWidgetSettings => ({
  widget_key: 'bible_daily',
  visibility: 'visible',
  order_index: 0,
  size: 'medium',
  is_pinned: false,
  sort_mode: null,
  pinned_devices: [],
  config: {},
  updated_at: 0,
  ...overrides,
});

const renderWidget = (overrides: Partial<UserWidgetSettings> = {}) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <MemoryRouter>
      <BibleDailyWidget
        settingsButton={<span data-testid="settings-button" />}
        userSettings={settings(overrides)}
        onTogglePin={() => {}}
      />
    </MemoryRouter>
  );
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

const mockedApi = vi.mocked(api);

beforeEach(() => {
  navigate.mockReset();
  mockedApi.getBibleDaily.mockReset().mockResolvedValue(daily());
});

describe('BibleDailyWidget', () => {
  it('shows the verse, its reference, the devotional and the read streak', async () => {
    renderWidget();

    expect(await screen.findByTestId('bible-daily-verse')).toHaveTextContent('John 3:16');
    expect(screen.getByTestId('bible-daily-verse')).toHaveTextContent(/For God so loved the world/);
    expect(screen.getByTestId('bible-daily-devotional')).toHaveTextContent('Day by Day by Grace');
    expect(screen.getByTestId('bible-daily-streak')).toHaveTextContent('4 day read streak');
  });

  it('costs exactly one request to paint the card', async () => {
    renderWidget();
    await screen.findByTestId('bible-daily-verse');
    expect(mockedApi.getBibleDaily).toHaveBeenCalledTimes(1);
  });

  it('opens the chapter behind the verse, not just the app', async () => {
    renderWidget();
    fireEvent.click(await screen.findByTestId('bible-daily-verse'));
    expect(navigate).toHaveBeenCalledWith('/bible?ref=John%203%3A16');
  });

  it('opens the reader without a reference from the Read button', async () => {
    renderWidget();
    fireEvent.click(await screen.findByRole('button', { name: 'Read' }));
    expect(navigate).toHaveBeenCalledWith('/bible');
  });

  it('follows a link devotional out to its source instead of the reader', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    renderWidget();
    fireEvent.click(await screen.findByTestId('bible-daily-devotional'));
    expect(open).toHaveBeenCalledWith(
      'https://bible.test/devotionals/dbdbg/view.cfm?doy=276',
      '_blank',
      'noopener,noreferrer'
    );
    expect(navigate).not.toHaveBeenCalled();
    open.mockRestore();
  });

  it('drops the devotional and streak in the 1x1 size so the verse still fits', async () => {
    renderWidget({ size: 'small' });
    await screen.findByTestId('bible-daily-verse');
    expect(screen.queryByTestId('bible-daily-devotional')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bible-daily-streak')).not.toBeInTheDocument();
  });

  it('shows the server\'s message when no corpus is imported, with a retry', async () => {
    const message = 'No Bible text is imported. Load a public-domain translation with `python -m services.bible.import_corpus`.';
    mockedApi.getBibleDaily.mockRejectedValue(new Error(message));

    renderWidget();

    const error = await screen.findByTestId('bible-daily-error', {}, { timeout: 5000 });
    expect(error).toHaveTextContent(/No Bible text is imported/);
    fireEvent.click(screen.getByRole('button', { name: /Try again/ }));
    await waitFor(() => expect(mockedApi.getBibleDaily).toHaveBeenCalledTimes(3), { timeout: 5000 });
  });

  it('surfaces a verse-level failure rather than an empty card', async () => {
    mockedApi.getBibleDaily.mockResolvedValue(daily({
      verse_of_day: { error: 'No verses are loaded for scope "ot". Import a translation that covers the Old Testament.' },
    }));

    renderWidget();

    expect(await screen.findByTestId('bible-daily-error')).toHaveTextContent(/Import a translation/);
    expect(screen.queryByTestId('bible-daily-verse')).not.toBeInTheDocument();
  });

  it('does not invent a streak when the payload has none', async () => {
    mockedApi.getBibleDaily.mockResolvedValue(daily({ streaks: undefined }));
    renderWidget();
    expect(await screen.findByTestId('bible-daily-streak')).toHaveTextContent('Reading streak unavailable');
  });
});