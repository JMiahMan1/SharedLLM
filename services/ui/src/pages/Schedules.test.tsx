import { beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/setup';
import { renderWithProviders } from '../test/render';
import Schedules from './Schedules';

const THERMOSTAT = {
  entity_id: 'climate.hall', name: 'Hall', area: null, state: 'heat', current_temperature: 19.5, temperature: 20,
  hvac_action: 'heating', capabilities: { hvac_modes: ['off', 'heat'], fan_modes: [], min_temp: 7, max_temp: 30, unit: '°C' },
  current: { program_id: 'p1', program_name: 'Made-up week', slot: 0, start: '2026-11-11T06:00:00', end: null, setpoint: { hvac_mode: 'heat', temperature: 20 } },
  next: { program_id: 'p1', program_name: 'Made-up week', slot: 1, at: '2026-11-11T22:00:00', setpoint: { hvac_mode: 'heat', temperature: 17 } },
  hold: null, effective: { source: 'schedule', setpoint: { hvac_mode: 'heat', temperature: 20 } },
  summary: 'Following Made-up week: 20 heat, goes to 17 heat at 10:00 PM',
};
const OVERVIEW = {
  thermostats: [THERMOSTAT],
  programs: [{
    id: 'p1', name: 'Made-up week', entity_ids: ['climate.hall'], weekdays: ['daily'], start_date: null, end_date: null,
    enabled: true, slots: [{ start: '06:00:00', hvac_mode: 'heat', temperature: 20 }, { start: '22:00:00', hvac_mode: 'heat', temperature: 17 }],
  }],
  settings: null, away: { active: false, reason: null }, energy: { in_window: false, window: null, power_kw: null, shed: [] },
  can_edit: false, now: '2026-11-11T12:00:00', time_zone: 'UTC',
};

let holds: unknown[] = [];

beforeEach(() => {
  holds = [];
  server.use(
    http.get('/api/homehub/climate', () => HttpResponse.json(OVERVIEW)),
    http.post('/api/homehub/climate/hold', async ({ request }) => {
      holds.push(await request.json());
      return HttpResponse.json(THERMOSTAT);
    }),
  );
});

describe('Schedules page', () => {
  it('shows a thermostat with its summary and week, without edit controls for a family member', async () => {
    renderWithProviders(<Schedules />, { route: '/schedules' });
    const card = await screen.findByRole('article', { name: 'Hall' });
    expect(within(card).getByTestId('next-change')).toHaveTextContent('goes to 17 heat');
    expect(within(card).getAllByTestId('timeline-slot').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /New program/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Away and energy/ })).toBeNull();
  });

  it('lets anyone hold until the next change', async () => {
    renderWithProviders(<Schedules />, { route: '/schedules' });
    const card = await screen.findByRole('article', { name: 'Hall' });
    await userEvent.click(within(card).getByRole('button', { name: 'Hold a temperature' }));
    const temp = within(card).getByRole('spinbutton', { name: /Temperature/ });
    await userEvent.clear(temp);
    await userEvent.type(temp, '22');
    expect(within(card).getByRole('radio', { name: /Until the next change/ })).toBeChecked();
    await userEvent.click(within(card).getByRole('button', { name: 'Hold' }));
    await waitFor(() => expect(holds).toEqual([
      { entity_id: 'climate.hall', setpoint: { hvac_mode: 'heat', temperature: 22 }, until: 'next_slot' },
    ]));
  });
});
