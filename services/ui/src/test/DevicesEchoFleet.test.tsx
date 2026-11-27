import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import Devices from '../pages/Devices';
import { EchoDropInClient } from '../lib/echoDropInClient';
import { renderWithProviders } from './render';
import { server } from './setup';

// Whole-page Devices imports SharedFilesPanel -> PdfViewer, whose pdfjs worker
// URL is irrelevant to the fleet. Keep it out of this suite's module graph.
vi.mock('../components/workspace/viewers/PdfViewer', () => ({ PdfViewer: () => null }));

interface HaServiceCall {
  domain: string;
  service: string;
  entity_id: string;
  service_data?: Record<string, unknown>;
}

const state = (
  entity_id: string,
  value: string,
  friendly_name?: string,
  attributes?: Record<string, unknown>,
) => ({
  entity_id,
  friendly_name: friendly_name ?? '',
  state: value,
  domain: entity_id.split('.', 1)[0] ?? '',
  attributes,
});

/** Two adopted Echo nodes as Home Hub publishes them through /api/entities. */
const ECHO_STATES = [
  state('sensor.kitchen_echo_drop_in', 'idle', 'Kitchen Echo Drop In'),
  state('assist_satellite.kitchen_echo', 'idle', 'Kitchen Echo'),
  state('input_boolean.kitchen_echo_dnd', 'off'),
  state('sensor.bedroom_echo_drop_in', 'idle', 'Bedroom Echo Drop In'),
  state('assist_satellite.bedroom_echo', 'idle', 'Bedroom Echo'),
  state('select.bedroom_echo_wake_word', 'Alexa', 'Bedroom Echo Wake Word', { options: ['Alexa', 'Jarvis'] }),
  state('light.living_room', 'on'), // unrelated hub entity: not a ghost Echo
];

beforeEach(() => {
  window.location.hash = '#devices';
  server.use(http.get('/api/entities', () => HttpResponse.json({ entities: ECHO_STATES })));
});

describe('Devices > Echo fleet', () => {
  it('lists discovered Echo nodes from Home Hub on the admin devices tab', async () => {
    renderWithProviders(<Devices />);

    const tabs = await screen.findByTestId('devices-sections');
    expect(within(tabs).getByRole('tab', { name: 'Your devices' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByTestId('echo-device-kitchen_echo')).toBeInTheDocument();
    expect(screen.getByTestId('echo-device-bedroom_echo')).toBeInTheDocument();
    expect(screen.getByTestId('echo-fleet-count')).toHaveTextContent('2 devices');
    expect(screen.queryByTestId('echo-device-living_room')).not.toBeInTheDocument();
    // The fleet lives between Hardware and Light Groups in the same tabpanel.
    const panel = screen.getByRole('tabpanel');
    expect(panel).toContainElement(document.getElementById('devices-hardware'));
    expect(panel).toContainElement(document.getElementById('devices-echo-fleet'));
    expect(panel).toContainElement(document.getElementById('devices-light-groups'));
  });

  it("opens the devices tab when the #devices-echo-fleet link is followed", async () => {
    window.location.hash = '#devices-echo-fleet';
    renderWithProviders(<Devices />, { route: '/devices#devices-echo-fleet' });

    const tabs = await screen.findByTestId('devices-sections');
    expect(within(tabs).getByRole('tab', { name: 'Your devices' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByTestId('echo-device-kitchen_echo')).toBeInTheDocument();
  });

  it('keeps the DND switch and Drop-In button working inside Devices', async () => {
    const user = userEvent.setup();
    const calls: HaServiceCall[] = [];
    server.use(http.post('/execute/ha_service', async ({ request }) => {
      calls.push((await request.json()) as HaServiceCall);
      return HttpResponse.json({ status: 'SUCCESS' });
    }));
    // The Drop-In button used to trigger an esphome HA service call; that path
    // was intentionally replaced by the WebSocket EchoDropInClient, so this
    // asserts the button now starts that client instead.
    const connectSpy = vi.spyOn(EchoDropInClient.prototype, 'connect').mockResolvedValue(undefined);
    localStorage.setItem('jarvis_api_key', 'test-token');
    renderWithProviders(<Devices />);

    const dnd = await screen.findByRole('switch', { name: 'Do Not Disturb for Kitchen Echo' });
    expect(dnd).toHaveAttribute('aria-checked', 'false');
    await user.click(dnd);
    expect(dnd).toHaveAttribute('aria-checked', 'true');
    await waitFor(() => expect(calls).toContainEqual(expect.objectContaining({
      domain: 'input_boolean', service: 'turn_on', entity_id: 'input_boolean.kitchen_echo_dnd',
    })));

    await user.click(screen.getByRole('button', { name: 'Drop In on Kitchen Echo' }));
    await waitFor(() => expect(connectSpy).toHaveBeenCalledTimes(1));
    // No esphome drop-in service call is issued anymore.
    expect(calls).not.toContainEqual(expect.objectContaining({
      domain: 'esphome', service: 'kitchen_echo_drop_in',
    }));
  });

  it('falls back to the Family tab without a fleet when Home Hub is disabled', async () => {
    window.location.hash = '#devices-echo-fleet';
    server.use(http.get('/api/integrations/status', () => HttpResponse.json({
      homehub: { enabled: false }, home_assistant: { enabled: true },
    })));
    renderWithProviders(<Devices />, { route: '/devices#devices-echo-fleet' });

    expect(await screen.findByText('Family devices')).toBeInTheDocument();
    expect(screen.queryByTestId('echo-fleet-count')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Echo fleet')).not.toBeInTheDocument();
    expect(document.getElementById('devices-tab-devices')).toBeNull();
    // With only the family tab left, the tablist itself is not rendered.
    expect(screen.queryByTestId('devices-sections')).not.toBeInTheDocument();
  });
});
