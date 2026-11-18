import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/setup';
import { renderWithProviders } from '../../test/render';
import ScheduleStatus from './ScheduleStatus';
import type { Thermostat } from '../../lib/schedules';

vi.mock('../ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));

const THERMOSTAT = {
  entity_id: 'climate.hall', name: 'Hall', area: null, state: 'heat', current_temperature: 19.5, temperature: 20,
  hvac_action: 'heating', capabilities: { hvac_modes: ['off', 'heat'], fan_modes: [], min_temp: 7, max_temp: 30, unit: '°C' },
  current: { program_id: 'p1', program_name: 'Made-up week', slot: 0, start: '2026-11-11T06:00:00', end: null, setpoint: { hvac_mode: 'heat', temperature: 20 } },
  next: null, hold: null, effective: { source: 'schedule', setpoint: { hvac_mode: 'heat', temperature: 20 } },
  summary: 'Following Made-up week: 20 heat',
} as unknown as Thermostat;

let switched: { id: string; body: unknown }[] = [];

beforeEach(() => {
  switched = [];
  server.use(
    http.post('/api/homehub/schedules/:id/enabled', async ({ params, request }) => {
      switched.push({ id: String(params.id), body: await request.json() });
      return HttpResponse.json({ schedule_id: params.id, enabled: false });
    }),
  );
});

describe('ScheduleStatus', () => {
  it('lets an admin turn off the program a thermostat follows', async () => {
    const changed = vi.fn();
    renderWithProviders(<ScheduleStatus thermostat={THERMOSTAT} onChanged={changed} canEdit />);
    await userEvent.click(screen.getByRole('button', { name: 'Turn off the Made-up week schedule' }));
    await waitFor(() => expect(switched).toEqual([{ id: 'p1', body: { enabled: false } }]));
    expect(changed).toHaveBeenCalled();
  });

  it('offers no off switch to a family member or on a compact card', () => {
    const { unmount } = renderWithProviders(<ScheduleStatus thermostat={THERMOSTAT} onChanged={() => {}} />);
    expect(screen.queryByRole('button', { name: /Turn off/ })).toBeNull();
    unmount();
    renderWithProviders(<ScheduleStatus thermostat={THERMOSTAT} onChanged={() => {}} canEdit compact />);
    expect(screen.queryByRole('button', { name: /Turn off/ })).toBeNull();
  });
});
