import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../test/setup';
import { renderWithProviders } from '../test/render';
import Automations from './Automations';

const ITEMS = [
  {
    kind: 'automation', id: 'porch_at_dusk', entity_id: 'automation.porch_at_dusk', alias: 'Porch at dusk', enabled: true,
    runnable: true, unsupported_reason: null, warnings: [], last_triggered: null, last_error: null, running: 1,
    entities: ['light.porch'],
  },
  {
    kind: 'automation', id: 'old_thing', entity_id: 'automation.old_thing', alias: 'Old thing', enabled: false,
    runnable: false, unsupported_reason: 'Device triggers are not supported yet', warnings: [], last_triggered: null,
    last_error: null, running: 0, entities: [],
  },
];
const CATALOG = { triggers: [], conditions: [], actions: [], services: [], entities: [], areas: [], zones: [], persons: [] };

function serve(canEdit: boolean) {
  server.use(
    http.get('/api/homehub/automations', () => HttpResponse.json({ items: ITEMS, can_edit: canEdit })),
    http.get('/api/homehub/automations/catalog', () => HttpResponse.json(CATALOG)),
    http.get('/api/homehub/automations/automation/porch_at_dusk', () => HttpResponse.json({
      item: ITEMS[0], yaml: 'alias: Porch at dusk\n',
      config: { alias: 'Porch at dusk', triggers: [{ trigger: 'time', at: '19:00:00' }], conditions: [], actions: [] },
    })),
  );
}

describe('Automations page', () => {
  beforeEach(() => serve(false));

  it('lists items with running and unsupported badges, read-only for a family member', async () => {
    renderWithProviders(<Automations />, { route: '/automations' });
    const rows = await screen.findAllByTestId('automation-row');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('Not supported yet', { exact: false })).toBeInTheDocument();
    expect(within(rows[1]).getByRole('status')).toHaveTextContent('Running');
    expect(screen.queryByRole('button', { name: /New/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Run .* now/ })).toBeNull();
    for (const sw of screen.getAllByRole('switch')) expect(sw).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: 'Open Porch at dusk' }));
    const editor = await screen.findByTestId('automation-editor');
    expect(within(editor).queryByRole('button', { name: /save/i })).toBeNull();
    expect(within(editor).getByText('Only an admin can change this.')).toBeInTheDocument();
  });

  it('shows an automation waiting for its device calmly, not as an error, and offers no run', async () => {
    const waiting = {
      kind: 'automation', id: 'screen_sync', entity_id: 'automation.screen_sync', alias: 'Screen sync', enabled: true,
      runnable: true, unsupported_reason: null, last_error: null, warnings: [], last_triggered: null, running: 0, entities: [],
      waiting_reason: 'needs esphome.wall_screen_set_weather, which no connected device offers',
    };
    server.use(http.get('/api/homehub/automations', () => HttpResponse.json({ items: [ITEMS[0], waiting], can_edit: true })));
    renderWithProviders(<Automations />, { route: '/automations' });
    await screen.findAllByTestId('automation-row');
    const row = screen.getByRole('button', { name: 'Open Screen sync' }).closest('li') as HTMLElement;
    const badge = within(row).getByTestId('automation-waiting');
    expect(badge).toHaveTextContent('Waiting for its device');
    expect(badge).toHaveTextContent('needs esphome.wall_screen_set_weather, which no connected device offers');
    expect(badge.className).not.toMatch(/red|amber/);
    expect(within(row).getByRole('switch')).toBeChecked();
    expect(within(row).queryByRole('button', { name: 'Run Screen sync now' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Run Porch at dusk now' })).toBeInTheDocument();
    const porch = screen.getByRole('button', { name: 'Open Porch at dusk' }).closest('li') as HTMLElement;
    expect(within(porch).queryByTestId('automation-waiting')).toBeNull();
  });

  it('searches', async () => {
    renderWithProviders(<Automations />, { route: '/automations' });
    await screen.findAllByTestId('automation-row');
    await userEvent.type(screen.getByRole('textbox', { name: 'Search automations' }), 'porch');
    expect(screen.getAllByTestId('automation-row')).toHaveLength(1);
  });

  it('gives an admin the edit controls', async () => {
    serve(true);
    renderWithProviders(<Automations />, { route: '/automations' });
    await screen.findAllByTestId('automation-row');
    expect(screen.getByRole('button', { name: /New/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run Porch at dusk now' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Run Old thing now' })).toBeNull();
  });
});
