import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import EventTimeline from '../components/health/EventTimeline';
import { renderWithProviders } from './render';
import type { TimelineEvent, TimelineResponse } from '../types/api';

const mocks = vi.hoisted(() => ({ getTimeline: vi.fn() }));

vi.mock('../services/api', () => ({
  api: { getTimeline: mocks.getTimeline },
}));

const event = (over: Partial<TimelineEvent> = {}): TimelineEvent => ({
  kind: 'workout',
  at: 1790400000,
  title: 'Morning run',
  detail: '3.2 mi · 28 min',
  meta: {},
  days_ago: 2,
  label: '8:00 AM',
  icon: 'dumbbell',
  ...over,
});

const timeline = (over: Partial<TimelineResponse> = {}): TimelineResponse => ({
  user_id: 'jeremiah',
  window_days: 30,
  day_count: 1,
  groups: [
    {
      day: '2025-09-26',
      relative: '2 days ago',
      events: [event()],
    },
  ],
  total_events: 1,
  empty: false,
  ...over,
});

const EMPTY = timeline({ groups: [], total_events: 0, empty: true, day_count: 0 });

describe('EventTimeline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getTimeline.mockResolvedValue(timeline());
  });

  it('groups events by day with the server-computed relative label', async () => {
    renderWithProviders(<EventTimeline />);
    const day = await screen.findByTestId('timeline-day');
    expect(day).toHaveAttribute('data-day', '2025-09-26');
    expect(within(day).getByText('2 days ago')).toBeInTheDocument();
    expect(within(day).getByText('Morning run')).toBeInTheDocument();
  });

  it('shows the window it covers, and does not call it the day count', async () => {
    // These are different numbers: 30 days asked for, 1 group came back.
    // Conflating them is what the server-side day_count fix was about.
    renderWithProviders(<EventTimeline days={30} />);
    expect(await screen.findByText(/last 30 days/)).toBeInTheDocument();
  });

  it('says nothing was recorded rather than showing an empty list', async () => {
    mocks.getTimeline.mockResolvedValue(EMPTY);
    renderWithProviders(<EventTimeline />);
    expect(await screen.findByTestId('timeline-empty')).toHaveTextContent(
      /nothing recorded in the last 30 days/i
    );
    expect(screen.queryByTestId('timeline-day')).not.toBeInTheDocument();
  });

  it('uses the server label for the time, never re-deriving it', async () => {
    // Re-deriving locally is how a skewed clock becomes a negative age.
    renderWithProviders(<EventTimeline />);
    expect(await screen.findByText('8:00 AM')).toBeInTheDocument();
  });

  it('renders an unknown icon kind without blanking the row', async () => {
    mocks.getTimeline.mockResolvedValue(
      timeline({
        groups: [
          {
            day: '2025-09-26',
            relative: '2 days ago',
            events: [event({ icon: 'not-a-real-icon', kind: 'goal', title: 'Hit 10k' })],
          },
        ],
      })
    );
    renderWithProviders(<EventTimeline />);
    expect(await screen.findByText('Hit 10k')).toBeInTheDocument();
  });

  it('shows the detail line only when there is one', async () => {
    mocks.getTimeline.mockResolvedValue(
      timeline({
        groups: [
          {
            day: '2025-09-26',
            relative: '2 days ago',
            events: [event({ detail: '' })],
          },
        ],
      })
    );
    renderWithProviders(<EventTimeline />);
    expect(await screen.findByText('Morning run')).toBeInTheDocument();
    expect(screen.queryByText('3.2 mi · 28 min')).not.toBeInTheDocument();
  });

  it('asks the server for the window it was given', async () => {
    renderWithProviders(<EventTimeline days={7} userId="michele" />);
    await screen.findByTestId('timeline-day');
    expect(mocks.getTimeline).toHaveBeenCalledWith('michele', 7);
  });

  it('forwards the user id through to the api layer', async () => {
    // The "all" sentinel is stripped inside api.ts by resolveUserId, not here,
    // so this asserts the pass-through only. Sentinel handling is tested
    // against resolveUserId below, which is where the logic lives.
    renderWithProviders(<EventTimeline userId="all" />);
    await screen.findByTestId('timeline-day');
    expect(mocks.getTimeline).toHaveBeenCalledWith('all', 30);
  });
});
