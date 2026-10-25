import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DigitalWellbeingCard from './DigitalWellbeingCard';
import { api } from '../../services/api';
import type { UsageDay } from '../../types/api';

const auth = vi.hoisted(() => ({ user: { username: 'dad', family_role: 'father', is_minor: false } as Record<string, unknown> }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), getUsage: vi.fn() } };
});

function today(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

const day = (overrides: Partial<UsageDay> = {}): UsageDay => ({
  day: today(),
  millis: 95 * 60000,
  first_use: new Date().setHours(6, 45, 0, 0),
  last_use: new Date().setHours(21, 10, 0, 0),
  sessions: 23,
  apps: [
    { package: 'com.spotify.music', millis: 20 * 60000 },
    { package: 'com.google.android.youtube', millis: 50 * 60000 },
  ],
  ...overrides,
});


const renderCard = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DigitalWellbeingCard />
    </QueryClientProvider>,
  );

describe('DigitalWellbeingCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { username: 'dad', family_role: 'father', is_minor: false };
    vi.mocked(api.getUsage).mockResolvedValue({ status: 'SUCCESS', days: [day()] });
  });

  it("shows an adult's own day: screen time, first and last use, top apps by name", async () => {
    renderCard();
    expect(await screen.findByTestId('wellbeing-screen-time')).toHaveTextContent('1 h 35 min');
    expect(screen.getByTestId('wellbeing-sessions')).toHaveTextContent('23');
    const apps = screen.getByRole('list', { name: 'Top apps today' });
    expect(apps.textContent).toMatch(/YouTube.*50 min.*Spotify.*20 min/);
    expect(api.getUsage).toHaveBeenCalledWith('dad', 14);
  });

  it('shows a minor nothing: their raw data is the parents’', () => {
    auth.user = { username: 'kid', family_role: 'child', is_minor: true };
    const { container } = renderCard();
    expect(container).toBeEmptyDOMElement();
    expect(api.getUsage).not.toHaveBeenCalled();
  });

  it('says plainly when nothing was reported today', async () => {
    vi.mocked(api.getUsage).mockResolvedValue({ status: 'SUCCESS', days: [] });
    renderCard();
    expect(await screen.findByTestId('digital-wellbeing-empty')).toHaveTextContent('No phone use reported today');
  });
});
