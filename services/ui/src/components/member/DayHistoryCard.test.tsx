import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DayHistoryCard from './DayHistoryCard';
import { api } from '../../services/api';
import { localDay } from '../../lib/healthHub';

vi.mock('../../services/api', async (orig) => {
  const actual = (await orig()) as { api: Record<string, unknown> };
  return { api: { ...actual.api, getPersonDay: vi.fn() } };
});

const t = (h: number, m = 0) => new Date(2026, 9, 31, h, m).getTime() / 1000;

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DayHistoryCard username="leo" name="Leo" />
    </QueryClientProvider>,
  );
}

describe('DayHistoryCard', () => {
  beforeEach(() => {
    vi.mocked(api.getPersonDay).mockImplementation(async (user, day) => ({
      user,
      day,
      points: [{ t: t(8), lat: 39.39, lon: -104.42 }, { t: t(15), lat: 39.59, lon: -104.42 }],
      stops: [],
      timeline: [
        { kind: 'stop', at: t(7), lat: 39.39, lon: -104.42, since: t(7), until: t(8, 5), fixes: 40, place: 'Home' },
        { kind: 'trip', at: t(8, 10), id: 'a', start_time: t(8, 10), end_time: t(8, 25), distance_miles: 4.26, duration_seconds: 900,
          start_location: { name: 'Home' }, end_location: { name: 'Lincoln Elementary' }, activity_type: 'driving', top_speed_mph: 44.6 },
        { kind: 'stop', at: t(8, 30), lat: 39.59, lon: -104.42, since: t(8, 30), until: t(15), fixes: 90, place: 'Lincoln Elementary' },
      ],
    }) as never);
  });

  it('lists the day in order: stops numbered, trips with distance and time', async () => {
    renderCard();
    await waitFor(() => expect(screen.getAllByTestId('day-history-stop')).toHaveLength(2));
    const [home, school] = screen.getAllByTestId('day-history-stop');
    expect(home).toHaveTextContent('1Home');
    expect(home).toHaveTextContent('1 h 5 min');
    expect(school).toHaveTextContent('2Lincoln Elementary');
    expect(screen.getByTestId('day-history-trip')).toHaveTextContent('Home to Lincoln Elementary');
    expect(screen.getByTestId('day-history-trip')).toHaveTextContent('4.3 mi · 15 min · top 45 mph');
    expect(api.getPersonDay).toHaveBeenCalledWith('leo', localDay());
  });

  it('steps back a day, never past today and never before the 30 days kept', async () => {
    renderCard();
    expect(screen.getByRole('button', { name: 'Next day' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Previous day' }));
    expect(screen.getByTestId('day-history-day')).toHaveTextContent('Yesterday');
    const d = new Date();
    d.setDate(d.getDate() - 1);
    await waitFor(() => expect(api.getPersonDay).toHaveBeenLastCalledWith('leo', localDay(d)));
    for (let i = 0; i < 40; i++) {
      const back = screen.getByRole('button', { name: 'Previous day' });
      if ((back as HTMLButtonElement).disabled) break;
      await userEvent.click(back);
    }
    const oldest = new Date();
    oldest.setDate(oldest.getDate() - 29);
    expect(vi.mocked(api.getPersonDay).mock.calls.at(-1)?.[1]).toBe(localDay(oldest));
  });

  it('says plainly when the person does not share their location', async () => {
    vi.mocked(api.getPersonDay).mockRejectedValue(Object.assign(new Error('no'), { isAxiosError: true, response: { status: 404 } }));
    renderCard();
    expect(await screen.findByTestId('member-day-history-error')).toHaveTextContent("Leo's location is not shared with you.");
  });
});
