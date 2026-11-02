import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FastingCard from './FastingCard';
import { api } from '../../services/api';
import type { FastingTraditions, HealthFast } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: {
      ...(actual.api as Record<string, unknown>),
      getHealthFasting: vi.fn(),
      startHealthFast: vi.fn(),
      stopHealthFast: vi.fn(),
      getFastingTraditions: vi.fn(),
      getBiblePassage: vi.fn(),
      getResources: vi.fn(),
    },
  };
});

const TRADITIONS: FastingTraditions = {
  kinds: [
    {
      key: 'station', name: 'Wednesday & Friday fast', era: '1st century onward', summary: 'The weekly fast.',
      rule: 'No food until 3 PM.', pattern: { type: 'until', clock: '15:00' },
      sources: [{ cite: 'Didache 8.1', note: 'Fast on Wednesday and Friday.' }], scripture: ['Matthew 9:15'],
    },
    {
      key: 'lent', name: 'Great Lent (the Forty Days)', era: '4th century', summary: 'Forty days.',
      rule: 'One meal toward evening.', pattern: { type: 'days', days: 40, daily: 'One meal toward evening, no meat' },
      sources: [{ cite: 'Council of Nicaea, Canon 5', note: 'Before Lent.' }], scripture: ['Matthew 4:1-4'],
    },
    { key: 'custom', name: 'My own fast', era: '', summary: 'A window you choose.', rule: '', pattern: { type: 'hours', hours: 16 }, sources: [], scripture: [] },
  ],
  why: ['Matthew 6:16-18'],
  heart: ['Isaiah 58:3-9'],
  psalms: ['Psalm 51'],
  fathers: [{ cite: 'John Chrysostom, Homilies on the Statues 3', note: 'Let the eye fast too.' }],
  prayer_hours: [{ clock: '09:00', name: 'Third hour', reason: 'Acts 2:15.', scripture: 'Acts 2:15' }],
  helps: ['Drink water.'],
  today: 'Joel 2:12-13',
};

/** A station fast as the server sends it: UTC instants, begun ten hours ago, ending in six. */
function running(): HealthFast {
  const now = Date.now();
  return {
    id: 4,
    started_at: new Date(now - 10 * 3_600_000).toISOString().replace('Z', '+00:00'),
    ended_at: null,
    target_hours: null,
    elapsed_minutes: 600,
    target_minutes: 960,
    percent: 62,
    remaining_minutes: 360,
    reached: false,
    kind: 'station',
    kind_name: 'Wednesday & Friday fast',
    ends_at: new Date(now + 6 * 3_600_000).toISOString().replace('Z', '+00:00'),
    days: null,
    intention: 'a friend in hospital',
  };
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <FastingCard />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getFastingTraditions).mockResolvedValue(TRADITIONS);
  vi.mocked(api.getResources).mockResolvedValue({ topic: 'fasting', books: [{ title: 'Great Lent', author: 'Alexander Schmemann' }], documents: [], passages: [] });
  vi.mocked(api.getBiblePassage).mockResolvedValue({
    version: 'NKJV', requested: 'Joel 2:12-13', reference: 'Joel 2:12-13', spans: [], count: 1,
    verses: [{ version: 'NKJV', osis: 'Joel', book_name: 'Joel', chapter: 2, verse: 12, reference: 'Joel 2:12', text: 'Turn to Me with all your heart, with fasting.' }],
  });
});

describe('FastingCard', () => {
  it('counts down to the end of a running fast, in local time, and ends it', async () => {
    const fast = running();
    vi.mocked(api.getHealthFasting).mockResolvedValue({ allowed: true, active: fast, recent: [], presets: [12, 14, 16] });
    vi.mocked(api.stopHealthFast).mockResolvedValue({ ...fast, ended_at: new Date().toISOString() });
    renderCard();

    expect(await screen.findByTestId('fasting-remaining')).toHaveTextContent(/^(5:59:\d\d|6:00:00)$/);
    // The start shows in this device's own time, not the UTC digits it was stored with.
    const began = new Date(fast.started_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    expect(screen.getByTestId('fasting-times')).toHaveTextContent(began);
    expect(screen.getByText('Wednesday & Friday fast', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText(/Praying for: a friend in hospital/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('fasting-stop'));
    await waitFor(() => expect(api.stopHealthFast).toHaveBeenCalled());
  });

  it('guides a running fast with its rule, Scripture, the Fathers and the library', async () => {
    vi.mocked(api.getHealthFasting).mockResolvedValue({ allowed: true, active: running(), recent: [], presets: [12, 14, 16] });
    renderCard();

    const guide = await screen.findByTestId('fasting-guide');
    expect(within(guide).getByTestId('fasting-kind-guide')).toHaveTextContent('Didache 8.1');
    expect(await within(guide).findByText(/Turn to Me with all your heart/)).toBeInTheDocument();
    expect(guide).toHaveTextContent('John Chrysostom');
    expect(await within(guide).findByTestId('fasting-library')).toHaveTextContent('Great Lent');
  });

  it('begins a fast of the kind chosen, asking those it is shared with to pray', async () => {
    vi.mocked(api.getHealthFasting).mockResolvedValue({ allowed: true, active: null, recent: [], presets: [12, 14, 16] });
    vi.mocked(api.startHealthFast).mockResolvedValue(running());
    renderCard();

    fireEvent.click(await screen.findByTestId('fast-kind-lent'));
    fireEvent.change(screen.getByTestId('fasting-intention'), { target: { value: 'our parish' } });
    fireEvent.click(screen.getByTestId('fasting-start'));
    await waitFor(() =>
      expect(api.startHealthFast).toHaveBeenCalledWith({
        kind: 'lent', target_hours: null, until: null, days: 40, intention: 'our parish', notify_shared: true, prayer_reminders: true,
      }),
    );
  });

  it('begins a window of hours for one of my own', async () => {
    vi.mocked(api.getHealthFasting).mockResolvedValue({ allowed: true, active: null, recent: [], presets: [12, 14, 16] });
    vi.mocked(api.startHealthFast).mockResolvedValue(running());
    renderCard();

    fireEvent.click(await screen.findByTestId('fast-kind-custom'));
    fireEvent.click(screen.getByRole('button', { name: '14 h' }));
    fireEvent.click(screen.getByTestId('fasting-start'));
    await waitFor(() => expect(api.startHealthFast).toHaveBeenCalledWith(expect.objectContaining({ kind: 'custom', target_hours: 14, until: null, days: null })));
  });

  it('renders nothing for a minor', async () => {
    vi.mocked(api.getHealthFasting).mockResolvedValue({ allowed: false, active: null, recent: [], presets: [12, 14, 16] });
    const { container } = renderCard();
    await waitFor(() => expect(api.getHealthFasting).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector('[data-testid="fasting-card"]')).toBeNull());
    expect(container.querySelector('.skeleton')).toBeNull();
  });
});
