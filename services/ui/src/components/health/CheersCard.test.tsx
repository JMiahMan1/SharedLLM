import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CheersCard from './CheersCard';
import { api } from '../../services/api';
import { healthDayLines, healthDayShareText } from '../../lib/healthShare';
import type { HealthCheersDay, HealthSummary } from '../../types/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), getHealthCheers: vi.fn(), sendHealthCheer: vi.fn() },
  };
});

vi.mock('../chat/familyDirectory', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../chat/familyDirectory')>()),
  useFamilyDirectory: () => (id?: string | null) => (id === 'mom' ? { username: 'mom', display_name: 'Mom' } : undefined),
}));

vi.mock('../../lib/healthHub', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/healthHub')>()),
  localDay: () => '2026-10-22',
}));

const KINDS = [
  { kind: 'well_done', emoji: '👏', label: 'Well done' },
  { kind: 'proud', emoji: '⭐', label: 'Proud of you' },
];

function day(overrides: Partial<HealthCheersDay> = {}): HealthCheersDay {
  return { day: '2026-10-22', kinds: KINDS, can_cheer: ['mom'], received: [], sent: [], ...overrides };
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CheersCard />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('CheersCard', () => {
  it('lists cheers received and sends one', async () => {
    vi.mocked(api.getHealthCheers).mockResolvedValue(
      day({
        received: [{ id: 1, from_user: 'mom', to_user: 'me', day: '2026-10-22', kind: 'proud', emoji: '⭐', label: 'Proud of you', created_at: '' }],
      }),
    );
    vi.mocked(api.sendHealthCheer).mockResolvedValue({
      id: 2, from_user: 'me', to_user: 'mom', day: '2026-10-22', kind: 'well_done', emoji: '👏', label: 'Well done', created_at: '', duplicate: false,
    });
    renderCard();

    expect(await screen.findByText('⭐ Mom: Proud of you')).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId('cheer-mom')).getByRole('button', { name: 'Well done to Mom' }));
    await waitFor(() => expect(api.sendHealthCheer).toHaveBeenCalledWith({ to_user: 'mom', kind: 'well_done', day: '2026-10-22' }));
  });

  it('is absent when there is nobody to cheer and nothing received', async () => {
    vi.mocked(api.getHealthCheers).mockResolvedValue(day({ can_cheer: [] }));
    const { container } = renderCard();
    await waitFor(() => expect(api.getHealthCheers).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector('[data-testid="cheers-card"]')).toBeNull());
  });
});

describe('the shared health card', () => {
  const summary = {
    profile: { metrics: { sleep: true, steps: true, water: true, checkin: true, nutrition: true } },
    sleep: { last_night: { duration_minutes: 450 }, score: { label: 'rested' } },
    water: { logged: true, ml: 2100, percent: 105 },
    checkin: { energy: 4 },
    nutrition: { totals: { calories: 1800 } },
  } as unknown as HealthSummary;

  it('carries sleep, steps, water and energy, never calories', () => {
    const text = healthDayShareText({ summary, steps: 6200 }, 'Good day!');
    expect(text).toBe(
      'Good day!\n\n💚 My day so far:\n😴 Slept 7h 30m (rested)\n👟 6,200 steps so far\n💧 2,100 ml of water - goal reached\n⚡ Feeling good',
    );
    expect(text).not.toMatch(/kcal|calorie|1800|1,800/);
  });

  it('leaves out a card the person switched off', () => {
    const off = { ...summary, profile: { metrics: { ...summary.profile.metrics, sleep: false } } } as unknown as HealthSummary;
    expect(healthDayLines({ summary: off, steps: null }).some((line) => line.includes('Slept'))).toBe(false);
  });
});
