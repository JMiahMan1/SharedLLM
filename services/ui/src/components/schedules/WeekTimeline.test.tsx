import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import WeekTimeline from './WeekTimeline';
import type { Program } from '../../lib/schedules';

const WEEK: Program = {
  id: 'p1', name: 'Made-up week', entity_ids: ['climate.hall'], weekdays: ['workday'], start_date: null, end_date: null,
  enabled: true,
  slots: [
    { start: '22:00:00', hvac_mode: 'heat', temperature: 17 },
    { start: '06:00:00', hvac_mode: 'heat', temperature: 21 },
  ],
};

describe('WeekTimeline', () => {
  it('draws seven days with the covering program and taps a slot by its sorted index', async () => {
    const onSlot = vi.fn();
    render(<WeekTimeline week={[WEEK, WEEK, WEEK, WEEK, WEEK, null, null]} todayIndex={2} onSlot={onSlot} />);
    expect(screen.getAllByRole('group')).toHaveLength(7);
    expect(screen.getAllByText('No program')).toHaveLength(2);
    // Per covered day: the carried-over night, the day slot, the evening.
    expect(screen.getAllByTestId('timeline-slot')).toHaveLength(15);
    const day = screen.getAllByTestId('timeline-slot')[1];
    expect(day.getAttribute('aria-label')).toContain('21° Heat');
    expect(day.style.left).toBe('25%');
    await userEvent.click(day);
    expect(onSlot).toHaveBeenCalledWith(WEEK, 0, 0);
  });

  it('cannot be tapped without a handler', () => {
    render(<WeekTimeline week={[WEEK, null, null, null, null, null, null]} />);
    for (const b of screen.getAllByTestId('timeline-slot')) expect(b).toBeDisabled();
  });
});
