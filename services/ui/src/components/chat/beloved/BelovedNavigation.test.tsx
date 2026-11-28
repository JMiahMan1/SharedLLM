import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import BelovedPage from './BelovedPage';
import { DEFAULT_BELOVED_PREFS } from '../../../lib/beloved/activity';

/**
 * How someone finds their way around Beloved.
 *
 * The page used to open on a six-column strip of 11px words at the top, so the
 * only way to know Notes existed was to count tabs on a phone. Now the main
 * screen names every section with what is in it, the bar is at the bottom under
 * a thumb, and a notice can open the section it is about.
 */

const day = (id: string, date: string) => ({
  id, kind: 'day' as const, own: true, at: 1, updatedAt: 1,
  record: { v: 1, name: 'Our day', date, yearly: true },
});
const view = {
  profile: undefined,
  days: [day('d1', '2026-12-25'), day('d2', '2027-02-14')],
  notes: [],
  hearts: [],
  prayers: [],
  devotions: [],
  ideas: [],
  together: [],
  plans: [],
};

vi.mock('./useBeloved', () => ({
  useBeloved: () => ({
    token: 'abc',
    loading: false,
    error: null,
    me: { username: 'alex', display_name: 'Alex', avatar: null },
    couple: [
      { username: 'alex', display_name: 'Alex', me: true, avatar: null },
      { username: 'sam', display_name: 'Sam', me: false, avatar: null },
    ],
    spouse: { username: 'sam', display_name: 'Sam', avatar: null },
    family: [],
    view,
    activity: [],
    prefs: DEFAULT_BELOVED_PREFS,
    setPrefs: vi.fn(),
    save: vi.fn(async () => ({ notified: false })),
    sendHeart: vi.fn(async () => ({ notified: false })),
    remove: vi.fn(async () => undefined),
    refresh: vi.fn(async () => undefined),
  }),
  useVerse: () => ({ text: 'The Lord is my shepherd.', loading: false, failed: false }),
}));

const vault = { unlocked: true } as never;

function open(props: { initialTab?: 'home' | 'days' | 'today' | 'notes' | 'dates' | 'justus' | null; onClose?: () => void } = {}) {
  const onClose = props.onClose ?? vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BelovedPage token="abc" vault={vault} initialTab={props.initialTab ?? null} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

describe('finding your way around Beloved', () => {
  it('opens on the couple, with a bar of named sections at the bottom', () => {
    open();
    expect(screen.getByTestId('beloved-home')).toBeTruthy();
    for (const tab of ['home', 'days', 'today', 'notes', 'dates', 'justus']) {
      expect(screen.getByTestId(`beloved-tab-${tab}`)).toBeTruthy();
    }
    expect(screen.getByTestId('beloved-tab-notes').textContent).toContain('Notes');
    // The active one says so, to a screen reader as well as by colour.
    expect(screen.getByTestId('beloved-tab-home').getAttribute('aria-current')).toBe('page');
  });

  it('shows what is inside each section from the main screen', () => {
    open();
    const hub = screen.getByTestId('beloved-quick-sections');
    expect(hub).toBeTruthy();
    expect(screen.getByTestId('beloved-quick-days-count').textContent).toBe('2 days');
    // An empty section says so rather than showing a zero.
    expect(screen.getByTestId('beloved-quick-notes-count').textContent).toBe('Nothing yet');
    expect(screen.getByTestId('beloved-quick-today-count').textContent).toBe('Nothing yet');
  });

  it('a tile opens its section', () => {
    open();
    fireEvent.click(screen.getByTestId('beloved-quick-notes'));
    expect(screen.getByTestId('beloved-tab-notes').getAttribute('aria-current')).toBe('page');
    expect(screen.queryByTestId('beloved-home')).toBeNull();
  });

  it('opens the section a notice asked for', () => {
    open({ initialTab: 'notes' });
    expect(screen.queryByTestId('beloved-home')).toBeNull();
    expect(screen.getByTestId('beloved-tab-notes').getAttribute('aria-current')).toBe('page');
  });

  it('moves an already open page to the section a second notice names', () => {
    const { rerender } = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BelovedPage token="abc" vault={vault} initialTab="home" onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId('beloved-home')).toBeTruthy();
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BelovedPage token="abc" vault={vault} initialTab="dates" onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId('beloved-home')).toBeNull();
    expect(screen.getByTestId('beloved-tab-dates').getAttribute('aria-current')).toBe('page');
  });

  it('walks back to Notes for a second note even after the person tabbed away', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <BelovedPage token="abc" vault={vault} initialTab="notes" initialTabSeq={1} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId('beloved-tab-notes').getAttribute('aria-current')).toBe('page');
    // The person reads the notes, then goes back to Us of their own accord.
    fireEvent.click(screen.getByTestId('beloved-tab-home'));
    expect(screen.getByTestId('beloved-home')).toBeTruthy();
    // A second note arrives: same section name, so only the count says it is new.
    rerender(
      <QueryClientProvider client={client}>
        <BelovedPage token="abc" vault={vault} initialTab="notes" initialTabSeq={2} onClose={vi.fn()} />
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId('beloved-home')).toBeNull();
    expect(screen.getByTestId('beloved-tab-notes').getAttribute('aria-current')).toBe('page');
  });

  it('has a way back to the chat that says what it does', () => {
    const { onClose } = open();
    const back = screen.getByTestId('beloved-back');
    expect(back.getAttribute('aria-label')).toBe('Back to Private Chat');
    fireEvent.click(back);
    expect(onClose).toHaveBeenCalled();
  });
});
