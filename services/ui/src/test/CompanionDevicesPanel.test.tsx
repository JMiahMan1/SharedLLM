import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import CompanionDevicesPanel from '../components/settings/CompanionDevicesPanel';
import { renderWithProviders } from './render';
import { server } from './setup';

/**
 * Adding a companion device as the signed-in user. The server decides the
 * method from what the device can do: code (it has a screen) or adopt.
 */
let pairCalls: Array<Record<string, unknown>> = [];
let myDevices: Array<Record<string, unknown>> = [];
let method: 'code' | 'adopt' = 'code';

const mock = () => {
  server.use(
    http.get('/api/user-panel/devices', () => HttpResponse.json(myDevices)),
    http.post('/api/devices/pair', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      pairCalls.push(body);
      if (body.step === 'discover') {
        return HttpResponse.json({ status: 'SUCCESS', message: 'Found 1', service: 'esphome_pair',
          detail: { devices: [{ name: 'jarvis-watch', friendly_name: 'Jarvis Watch', host: '192.168.2.105', port: 6053 }] } });
      }
      if (body.step === 'start') {
        return HttpResponse.json({ status: 'SUCCESS', message: '', service: 'esphome_pair',
          detail: { method, friendly_name: method === 'code' ? 'Jarvis Watch' : 'Kitchen Speaker' } });
      }
      if (body.code && body.code !== '123456') {
        return HttpResponse.json({ status: 'FAILURE', message: 'Wrong code.', service: 'esphome_pair' });
      }
      myDevices = [...myDevices, { device_key: 'esphome:744dbd2c9728', owner_username: 'default', kind: method === 'code' ? 'watch' : 'assistant',
        label: method === 'code' ? 'Jarvis Watch' : 'Kitchen Speaker', registered_by: method === 'code' ? 'paired' : 'adopted' }];
      return HttpResponse.json({ status: 'SUCCESS', message: 'linked', service: 'esphome_pair',
        detail: { friendly_name: method === 'code' ? 'Jarvis Watch' : 'Kitchen Speaker' } });
    }),
  );
};

describe('CompanionDevicesPanel', () => {
  beforeEach(() => {
    pairCalls = [];
    myDevices = [
      { device_key: 'phone-abc12345', kind: 'phone', label: '', manufacturer: 'Google', model: 'Pixel 7', registered_by: 'self', owner_username: 'default' },
      { device_key: 'phone-kate0000000', kind: 'phone', label: "Kate's phone", registered_by: 'self', owner_username: 'kate' },
    ];
    method = 'code';
    mock();
  });

  it('lists only my own devices on my page', async () => {
    myDevices = [{ ...myDevices[0], app_version: '1.5.0' }, myDevices[1]];
    renderWithProviders(<CompanionDevicesPanel />);
    expect(await screen.findByText('Google Pixel 7')).toBeInTheDocument();
    expect(screen.getByText(/v1\.5\.0/)).toBeInTheDocument();
    expect(screen.getByText(/Signed in on this device/)).toBeInTheDocument();
    expect(screen.queryByText("Kate's phone")).not.toBeInTheDocument();
  });

  it('shows everyone, with owners, in the admin view', async () => {
    renderWithProviders(<CompanionDevicesPanel scope="all" />);
    expect(await screen.findByText("Kate's phone")).toBeInTheDocument();
    expect(((await screen.findByLabelText("Owner of Kate's phone")) as HTMLSelectElement).value).toBe('kate');
    expect((screen.getByLabelText('Owner of Google Pixel 7') as HTMLSelectElement).value).toBe('default');
  });

  it('lets an admin give a device to someone else, or unassign it', async () => {
    const patches: Array<{ key: string; body: Record<string, unknown> }> = [];
    server.use(
      http.get('/api/users', () => HttpResponse.json([
        { id: 1, username: 'default', role: 'admin' },
        { id: 2, username: 'kate', role: 'user' },
      ])),
      http.patch('/api/user-panel/devices/:key', async ({ params, request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        patches.push({ key: String(params.key), body });
        myDevices = myDevices.map((d) => (d.device_key === params.key ? { ...d, owner_username: body.owner_username || null } : d));
        return HttpResponse.json(myDevices.find((d) => d.device_key === params.key));
      }),
    );
    renderWithProviders(<CompanionDevicesPanel scope="all" />);
    const owner = (await screen.findByLabelText('Owner of Google Pixel 7')) as HTMLSelectElement;
    await waitFor(() => expect(owner.querySelectorAll('option')).toHaveLength(3));  // Unassigned, @default, @kate
    fireEvent.change(owner, { target: { value: 'kate' } });
    await waitFor(() => expect(patches).toEqual([{ key: 'phone-abc12345', body: { owner_username: 'kate' } }]));
    await waitFor(() => expect((screen.getByLabelText('Owner of Google Pixel 7') as HTMLSelectElement).value).toBe('kate'));
    fireEvent.change(screen.getByLabelText('Owner of Google Pixel 7'), { target: { value: '' } });
    await waitFor(() => expect(patches[1]).toEqual({ key: 'phone-abc12345', body: { owner_username: '' } }));
  });

  it('pairs a watch with the code it shows', async () => {
    renderWithProviders(<CompanionDevicesPanel />);
    fireEvent.click(await screen.findByText('Add device'));
    fireEvent.click(await screen.findByText('Jarvis Watch'));
    const input = await screen.findByLabelText('Pairing code');
    fireEvent.change(input, { target: { value: '12a3456' } });  // digits only
    expect((input as HTMLInputElement).value).toBe('123456');
    fireEvent.click(screen.getByRole('button', { name: /Pair/ }));
    expect(await screen.findByText('Jarvis Watch is now linked to you.')).toBeInTheDocument();
    expect(pairCalls.map((c) => c.step)).toEqual(['discover', 'start', 'finish']);
    expect(pairCalls[2]).toMatchObject({ host: '192.168.2.105', code: '123456' });
    await waitFor(() => expect(screen.getByText(/Paired with its code/)).toBeInTheDocument());
  });

  it('keeps the code step open on a wrong code', async () => {
    renderWithProviders(<CompanionDevicesPanel />);
    fireEvent.click(await screen.findByText('Add device'));
    fireEvent.click(await screen.findByText('Jarvis Watch'));
    fireEvent.change(await screen.findByLabelText('Pairing code'), { target: { value: '000000' } });
    fireEvent.click(screen.getByRole('button', { name: /Pair/ }));
    await waitFor(() => expect(pairCalls.length).toBe(3));
    expect(screen.getByLabelText('Pairing code')).toBeInTheDocument();
  });

  it('adopts a device that has no screen, without a code', async () => {
    method = 'adopt';
    renderWithProviders(<CompanionDevicesPanel />);
    fireEvent.click(await screen.findByText('Add device'));
    fireEvent.change(await screen.findByLabelText('Device address'), { target: { value: '10.0.0.40' } });
    fireEvent.click(screen.getByRole('button', { name: /Find/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Link to me/ }));
    expect(await screen.findByText('Kitchen Speaker is now linked to you.')).toBeInTheDocument();
    expect(pairCalls[2]).toMatchObject({ step: 'finish', host: '10.0.0.40' });
    expect(pairCalls[2].code).toBeUndefined();
  });
});
