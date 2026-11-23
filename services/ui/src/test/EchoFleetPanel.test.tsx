import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import EchoFleetPanel from '../components/devices/EchoFleetPanel';
import { extractEchoFleet, isSafeHttpUrl, type EchoDeviceSummary, type EchoState } from '../lib/echoFleet';
import { server } from './setup';

interface HaServiceCall {
  domain: string;
  service: string;
  entity_id: string;
  service_data?: Record<string, unknown>;
}

const state = (entity_id: string, value: string, attributes?: Record<string, unknown>): EchoState => ({
  entity_id, state: value, attributes,
});

const CROWN: EchoState[] = [
  state('sensor.crown_drop_in', 'idle', { friendly_name: 'Crown Drop In' }),
  state('assist_satellite.crown', 'idle', { friendly_name: 'Echo Show Crown' }),
  state('input_boolean.crown_dnd', 'off'),
  state('select.crown_wake_word', 'Alexa', { options: ['Alexa', 'Jarvis', 'None'] }),
  state('light.crown_lights', 'off'),
  state('sensor.crown_web_ui_address', 'http://crown.local:28931'),
  state('sensor.crown_sendspin_address', '192.0.2.60:28928'),
  state('update.crown_firmware', 'on', { installed_version: '1.2.0', latest_version: '2.0.0' }),
  state('binary_sensor.crown_last_request_whispered', 'off'),
];

const DONUT: EchoState[] = [
  state('assist_satellite.donut_assist_satellite', 'idle', { friendly_name: 'Kitchen Dot' }),
  state('input_boolean.donut_dnd', 'on'),
  state('sensor.donut_web_ui_address', 'javascript:alert(1)'),
];

function renderPanel(states?: EchoState[], onDropIn?: (device: EchoDeviceSummary) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const result = render(
    <QueryClientProvider client={client}>
      <EchoFleetPanel states={states} onDropIn={onDropIn} />
    </QueryClientProvider>,
  );
  return { ...result, client, invalidate };
}

async function captureHaServices(): Promise<HaServiceCall[]> {
  const calls: HaServiceCall[] = [];
  server.use(
    http.post('/execute/ha_service', async ({ request }) => {
      calls.push((await request.json()) as HaServiceCall);
      return HttpResponse.json({ status: 'SUCCESS' });
    }),
  );
  return calls;
}

describe('EchoFleetPanel', () => {
  it('groups Home Hub states by Echo node and reads its controls', () => {
    const fleet = extractEchoFleet([...CROWN, ...DONUT, state('light.living_room', 'on')]);

    expect(fleet).toHaveLength(2);
    const crown = fleet.find((device) => device.node === 'crown');
    expect(crown).toMatchObject({
      name: 'Echo Show Crown',
      satelliteEntityId: 'assist_satellite.crown',
      dropInEntityId: 'sensor.crown_drop_in',
      dndEntityId: 'input_boolean.crown_dnd',
      muteEntityId: null,
      wakeWord: 'Alexa',
      wakeWordOptions: ['Alexa', 'Jarvis', 'None'],
      lightRingEntityId: 'light.crown_lights',
      webUiUrl: 'http://crown.local:28931',
      sendspinAddress: '192.0.2.60:28928',
      updateAvailable: true,
      installedVersion: '1.2.0',
      latestVersion: '2.0.0',
      whisperEnabled: true,
      online: true,
    });
    const donut = fleet.find((device) => device.node === 'donut');
    expect(donut?.name).toBe('Kitchen Dot');
    expect(donut?.dndOn).toBe(true);
  });

  it('never invents a ghost Echo from a hassmic button and reads its own drop-in entities', () => {
    const states: EchoState[] = [
      state('sensor.kitchen_drop_in', 'idle', { friendly_name: 'Kitchen Drop In' }),
      state('button.kitchen_end_drop_in', 'off', { friendly_name: 'Kitchen End Drop In' }),
      state('sensor.kitchen_drop_in_with', 'Dad', { friendly_name: 'Kitchen Drop In With' }),
      state('switch.kitchen_microphone', 'off'), // hassmic: off == muted
    ];
    const fleet = extractEchoFleet(states);

    expect(fleet).toHaveLength(1);
    expect(fleet[0]).toMatchObject({
      node: 'kitchen',
      name: 'Kitchen',
      dropInEntityId: 'sensor.kitchen_drop_in',
      endDropInEntityId: 'button.kitchen_end_drop_in',
      dropInWith: 'Dad',
      muteEntityId: 'switch.kitchen_microphone',
      muted: true,
    });

    renderPanel(states);
    expect(screen.queryByTestId('echo-device-kitchen_end')).not.toBeInTheDocument();
    expect(screen.getByTestId('echo-fleet-count')).toHaveTextContent('1 device');
    expect(screen.getByText(/Drop-In with Dad/)).toBeInTheDocument();
  });

  it('falls back to the drop-in sensor attribute and reads mute/muted switches', () => {
    const fleet = extractEchoFleet([
      state('sensor.crown_drop_in', 'idle', { drop_in_with: 'Mom' }),
      state('binary_sensor.crown_muted', 'on'),
      state('switch.crown_microphone', 'on'), // on == not muted; the muted switch still wins
    ]);
    expect(fleet[0]).toMatchObject({
      dropInWith: 'Mom', muteEntityId: 'binary_sensor.crown_muted', muted: true,
    });
  });

  it('accepts only http(s) URLs for the settings link and blocks javascript:', () => {
    expect(isSafeHttpUrl('https://crown.local:28931')).toBe(true);
    expect(isSafeHttpUrl('javascript:alert(1)')).toBe(false);
    expect(isSafeHttpUrl('data:text/html,hi')).toBe(false);
    const fleet = extractEchoFleet(DONUT);
    expect(fleet[0].webUiUrl).toBeNull();

    renderPanel(DONUT);
    expect(screen.queryByRole('link', { name: /Echo Settings/i })).not.toBeInTheDocument();
    expect(screen.getByText(/not a safe http\(s\) address/i)).toBeInTheDocument();
  });

  it('toggles Do Not Disturb through the hub and shows the new state', async () => {
    const user = userEvent.setup();
    const calls = await captureHaServices();
    renderPanel(CROWN);

    const toggle = screen.getByRole('switch', { name: 'Do Not Disturb for Echo Show Crown' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await user.click(toggle);

    expect(toggle).toHaveAttribute('aria-checked', 'true');
    await waitFor(() => expect(calls).toContainEqual(expect.objectContaining({
      domain: 'input_boolean', service: 'turn_on', entity_id: 'input_boolean.crown_dnd',
    })));
  });

  it('starts Drop-In with the ESPHome service and ends it with the hassmic button', async () => {
    const user = userEvent.setup();
    const calls = await captureHaServices();
    const first = renderPanel(CROWN);

    await user.click(screen.getByRole('button', { name: 'Drop In on Echo Show Crown' }));
    await waitFor(() => expect(calls).toContainEqual(expect.objectContaining({
      domain: 'esphome', service: 'crown_drop_in', entity_id: 'sensor.crown_drop_in',
      service_data: { target: 'crown' },
    })));
    expect(calls.some((call) => call.domain === 'hassmic')).toBe(false);

    const connectedStates = [
      ...CROWN.filter((entry) => entry.entity_id !== 'sensor.crown_drop_in'),
      state('sensor.crown_drop_in', 'connected', { friendly_name: 'Crown Drop In' }),
      state('sensor.crown_drop_in_with', 'Mom'),
      state('button.crown_end_drop_in', 'off', { friendly_name: 'Crown End Drop In' }),
    ];
    expect(extractEchoFleet(connectedStates)[0]).toMatchObject({
      dropInState: 'connected', dropInWith: 'Mom', endDropInEntityId: 'button.crown_end_drop_in',
    });
    first.unmount();
    renderPanel(connectedStates);
    expect(screen.getByRole('button', { name: 'End Drop In on Echo Show Crown' })).toBeInTheDocument();
    expect(screen.getByText(/Drop-In with Mom/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'End Drop In on Echo Show Crown' }));
    await waitFor(() => expect(calls).toContainEqual(expect.objectContaining({
      domain: 'button', service: 'press', entity_id: 'button.crown_end_drop_in',
    })));
  });

  it('uses an onDropIn callback for idle Drop-In when supplied', async () => {
    const user = userEvent.setup();
    const calls = await captureHaServices();
    const onDropIn = vi.fn<(device: EchoDeviceSummary) => Promise<void>>(async () => {});
    renderPanel(CROWN, onDropIn);

    await user.click(screen.getByRole('button', { name: 'Drop In on Echo Show Crown' }));
    await waitFor(() => expect(onDropIn).toHaveBeenCalledWith(expect.objectContaining({ node: 'crown' })));
    expect(calls).toHaveLength(0);
  });

  it('names the missing end_drop_in button instead of calling a nonexistent hassmic service', async () => {
    const user = userEvent.setup();
    const calls = await captureHaServices();
    renderPanel([
      ...CROWN.filter((entry) => entry.entity_id !== 'sensor.crown_drop_in'),
      state('sensor.crown_drop_in', 'connected', { friendly_name: 'Crown Drop In' }),
    ]);

    await user.click(screen.getByRole('button', { name: 'End Drop In on Echo Show Crown' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/no end_drop_in button entity/i);
    expect(calls).toHaveLength(0);
  });

  it('updates the wake word select immediately after a successful change', async () => {
    const user = userEvent.setup();
    await captureHaServices();
    renderPanel(CROWN);

    const select = screen.getByRole('combobox', { name: 'Wake word for Echo Show Crown' }) as HTMLSelectElement;
    expect(select.value).toBe('Alexa');
    await user.selectOptions(select, 'Jarvis');
    expect(select.value).toBe('Jarvis');
  });

  it('invalidates the fleet states after every control action', async () => {
    const user = userEvent.setup();
    await captureHaServices();
    const { invalidate } = renderPanel([
      ...CROWN,
      state('button.crown_end_drop_in', 'off'),
    ]);

    await user.click(screen.getByRole('switch', { name: 'Do Not Disturb for Echo Show Crown' }));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Wake word for Echo Show Crown' }), 'Jarvis');
    await user.click(screen.getByRole('button', { name: 'Drop In on Echo Show Crown' }));
    await user.click(screen.getByRole('button', { name: 'Install firmware update for Echo Show Crown' }));

    await waitFor(() => {
      const refreshes = invalidate.mock.calls.filter(
        ([filters]) => JSON.stringify(filters?.queryKey) === JSON.stringify(['echo-fleet', 'states']),
      );
      expect(refreshes).toHaveLength(4);
    });
  });

  it('offers firmware install only when the hub reports an update', async () => {
    const user = userEvent.setup();
    const calls = await captureHaServices();
    renderPanel(CROWN);

    expect(screen.getByText('Update available')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Install firmware update for Echo Show Crown' }));
    await waitFor(() => expect(calls).toContainEqual(expect.objectContaining({
      domain: 'update', service: 'install', entity_id: 'update.crown_firmware',
    })));
  });

  it('explains the empty state and the adoption projects', () => {
    renderPanel([]);
    expect(screen.getByText('No Echo devices found')).toBeInTheDocument();
    expect(screen.getByText(/donut/)).toBeInTheDocument();
    expect(screen.getByText(/crown/)).toBeInTheDocument();
    expect(screen.getAllByText(/echo-dot-assist/).length).toBeGreaterThan(0);
  });

  it('shows a clear banner when Home Hub states cannot be read', async () => {
    server.use(http.get('/api/entities', () => HttpResponse.json({
      status: 'FAILURE', message: 'Home Hub is offline',
    })));
    renderPanel();

    expect(await screen.findByRole('alert')).toHaveTextContent('Home Hub is unavailable: Home Hub is offline');
  });
});
