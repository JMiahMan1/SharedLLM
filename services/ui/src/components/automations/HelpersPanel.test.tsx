import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HelpersPanel from './HelpersPanel';
import * as helpers from '../../lib/helpers';
import type { HelperList, HelperRow } from '../../lib/helpers';

vi.mock('../../lib/helpers', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, listHelpers: vi.fn(), createHelper: vi.fn(), updateHelper: vi.fn(), deleteHelper: vi.fn(), callHelper: vi.fn() };
});
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const TIMER: HelperRow = {
  domain: 'timer', id: 'pasta', entity_id: 'timer.pasta', name: 'Pasta', config: { id: 'pasta', name: 'Pasta', duration: '0:10:00' },
  state: { entity_id: 'timer.pasta', state: 'idle', attributes: { duration: '0:10:00' } }, disabled: false,
};
const TOGGLE: HelperRow = {
  domain: 'input_boolean', id: 'guests', entity_id: 'input_boolean.guests', name: 'Guests', config: { id: 'guests', name: 'Guests' },
  state: { entity_id: 'input_boolean.guests', state: 'off', attributes: {} }, disabled: false,
};
const LIST: HelperList = {
  helpers: [TOGGLE, TIMER],
  types: [
    { domain: 'input_boolean', label: 'Toggle', services: ['turn_on'], fields: [{ name: 'initial', label: 'Starts as on', kind: 'boolean' }] },
    { domain: 'input_select', label: 'Dropdown', services: ['select_option'], fields: [{ name: 'options', label: 'Options', kind: 'options', required: true }] },
    { domain: 'timer', label: 'Timer', services: ['start'], fields: [{ name: 'duration', label: 'Duration (HH:MM:SS)', kind: 'duration', default: '0:00:00' }] },
  ],
  problems: ['counter Laps: step must be 1 or more'],
};

const wrap = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <HelpersPanel />
  </QueryClientProvider>,
);

describe('HelpersPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(helpers.listHelpers).mockResolvedValue(LIST);
  });

  it('lists helpers with their values, problems, and controls that call the hub', async () => {
    vi.mocked(helpers.callHelper).mockResolvedValue({ ...TOGGLE, state: { ...TOGGLE.state!, state: 'on' } });
    wrap();
    expect(await screen.findByText('Guests')).toBeTruthy();
    expect(screen.getByText('counter Laps: step must be 1 or more')).toBeTruthy();
    await userEvent.click(screen.getByRole('switch', { name: 'Guests on' }));
    expect(helpers.callHelper).toHaveBeenCalledWith('input_boolean', 'guests', 'turn_on', undefined);
    await waitFor(() => expect(screen.getAllByTestId('helper-value')[0].textContent).toBe('On'));
    await userEvent.type(screen.getByRole('textbox', { name: /Run Pasta for/ }), '0:00:30');
    await userEvent.click(screen.getByRole('button', { name: 'Start Pasta' }));
    expect(helpers.callHelper).toHaveBeenLastCalledWith('timer', 'pasta', 'start', { duration: '0:00:30' });
  });

  it('adds a dropdown with its options, and shows the hub\'s refusal', async () => {
    vi.mocked(helpers.createHelper).mockRejectedValueOnce({ response: { data: { detail: 'options must be different' } } });
    vi.mocked(helpers.createHelper).mockResolvedValueOnce(TOGGLE);
    wrap();
    await userEvent.click(await screen.findByRole('button', { name: /Add helper/ }));
    await userEvent.click(within(screen.getByTestId('helper-types')).getByRole('button', { name: /Dropdown/ }));
    const form = screen.getByTestId('helper-form');
    await userEvent.type(within(form).getByRole('textbox', { name: 'Name' }), 'House mode');
    await userEvent.type(within(form).getByRole('textbox', { name: 'New option' }), 'home{enter}');
    await userEvent.type(within(form).getByRole('textbox', { name: 'New option' }), 'away');
    await userEvent.click(within(form).getByRole('button', { name: /Add option/ }));
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }));
    expect(helpers.createHelper).toHaveBeenCalledWith('input_select', { name: 'House mode', options: ['home', 'away'] });
    expect(await within(form).findByRole('alert')).toBeTruthy();
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByTestId('helper-form')).toBeNull());
  });

  it('edits and deletes a helper', async () => {
    vi.mocked(helpers.updateHelper).mockResolvedValue(TIMER);
    wrap();
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Pasta' }));
    const form = screen.getByTestId('helper-form');
    const duration = within(form).getByRole('textbox', { name: /Duration/ });
    await userEvent.clear(duration);
    await userEvent.type(duration, '0:12:00');
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }));
    expect(helpers.updateHelper).toHaveBeenCalledWith('timer', 'pasta', { name: 'Pasta', duration: '0:12:00' });
    await userEvent.click(await screen.findByRole('button', { name: 'Edit Guests' }));
    await userEvent.click(within(screen.getByTestId('helper-form')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(helpers.deleteHelper).toHaveBeenCalledWith('input_boolean', 'guests'));
  });
});
