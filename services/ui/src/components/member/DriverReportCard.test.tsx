import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DriverReportCard from './DriverReportCard';
import { api } from '../../services/api';

vi.mock('../../services/api', async (orig) => {
  const actual = (await orig()) as { api: Record<string, unknown> };
  return { api: { ...actual.api, getDriverReport: vi.fn() } };
});

const none = { hard_brake: 0, rapid_accel: 0, high_speed: 0, phone_use: 0, phone_use_min: 0 };

const renderIt = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DriverReportCard username="zachary" name="Zachary" />
    </QueryClientProvider>,
  );

describe('DriverReportCard', () => {
  it('shows the week: drives, miles, time, top speed and each kind of event', async () => {
    vi.mocked(api.getDriverReport).mockResolvedValue({
      user: 'zachary', days: 7, trips: 4, miles: 31.24, drive_minutes: 75, top_speed_mph: 82.4,
      events: { hard_brake: 3, rapid_accel: 1, high_speed: 1, phone_use: 2, phone_use_min: 3.5 },
      per_trip: [{ id: 'a', start_time: 1_793_000_000, end_time: 1_793_000_900, distance_miles: 12.0, top_speed_mph: 82,
        start_location: { name: 'Home' }, end_location: { name: 'School' }, events: { ...none, hard_brake: 2, high_speed: 1 } }],
    } as never);
    renderIt();
    const totals = await screen.findByTestId('driving-totals');
    expect(totals).toHaveTextContent('Drives4');
    expect(totals).toHaveTextContent('Miles31.2');
    expect(totals).toHaveTextContent('Time1 h 15 min');
    expect(totals).toHaveTextContent('Top speed82 mph');
    const events = screen.getByTestId('driving-events');
    expect(events).toHaveTextContent('Hard braking3');
    expect(events).toHaveTextContent('Phone in use2 (3.5 min)');
    expect(screen.getByText('Home to School')).toBeInTheDocument();
    expect(screen.getByText('2 hard braking · 1 over 80 mph')).toBeInTheDocument();
  });

  it('says so when there was no driving', async () => {
    vi.mocked(api.getDriverReport).mockResolvedValue({ user: 'zachary', days: 7, trips: 0, miles: 0, drive_minutes: 0, top_speed_mph: 0, events: none, per_trip: [] } as never);
    renderIt();
    expect(await screen.findByTestId('member-driving-empty')).toHaveTextContent('Zachary has not driven in the last 7 days.');
  });
});
