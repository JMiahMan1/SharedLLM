import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import Devices from '../pages/Devices';
import { renderWithProviders } from './render';
import { server } from './setup';

/**
 * The device-management page (HANDOVER.md Phase 12): the inventory, the
 * update push, the confirmation-gated wipe, and the shared folders.
 */
const inventory = {
  devices: [
    {
      device_key: 'kid-phone-0001', kind: 'phone', label: "Kid's phone", manufacturer: 'Google', model: 'Pixel 7',
      os_version: '14', os_build: 'UQ1A', app_version: '1.9.0', app_build: '42', last_seen_at: '2026-10-25T10:00:00Z',
      first_seen_at: '2026-10-01T10:00:00Z', last_ip_address: '192.168.2.9', battery: null,
      capabilities: { guard: { deviceOwner: true, guardEnabled: true, webFilterActive: true, reportedAt: '2026-10-25T10:00:00Z' } },
      owner_username: 'kid', owner_display_name: 'Kid',
    },
  ],
  members: [
    { username: 'kid', display_name: 'Kid', device_count: 1, readable: true, reason: null },
    { username: 'dad', display_name: 'Dad', device_count: null, readable: false, reason: 'Only the father or an admin can see device details' },
  ],
  generated_at: '2026-10-25T10:00:00Z',
};

const folders = {
  folders: [
    { folder: 'school', files: [{ name: 'worksheet.pdf', size: 10, sha256: 'x', content_type: 'application/pdf', uploaded_by: 'dad', uploaded_at: '2026-10-25T09:00:00Z' }], pushed_to: ['kid'] },
    { folder: 'library', files: [], pushed_to: [] },
  ],
};

let pushed: Array<Record<string, unknown>> = [];
let wiped: string[] = [];

beforeEach(() => {
  pushed = [];
  wiped = [];
  server.use(
    http.get('/api/mdm/devices', () => HttpResponse.json(inventory)),
    http.get('/api/mdm/files', () => HttpResponse.json(folders)),
    http.post('/api/mdm/devices/update', async ({ request }) => {
      pushed.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ status: 'SUCCESS', sent: 1, owners: ['kid'], version: '2.0.0' });
    }),
    http.post('/api/mdm/devices/:key/wipe', ({ params }) => {
      wiped.push(String(params.key));
      return HttpResponse.json({ status: 'queued', delivery: 'the phone erases itself on its next check-in' });
    }),
  );
});

describe('Devices page', () => {
  it('lists every family device with its owner and guard state', async () => {
    renderWithProviders(<Devices />);
    expect(await screen.findByText("Kid's phone")).toBeInTheDocument();
    expect(screen.getByText(/1\.9\.0 \(build 42\)/)).toBeInTheDocument();
    expect(screen.getByTestId('mdm-guard-kid-phone-0001')).toBeInTheDocument();
    // A member identity will not hand over is shown as unreadable, not as empty.
    expect(screen.getByTestId('mdm-unreadable')).toHaveTextContent('Dad');
  });

  it('pushes an update notice to the selected device', async () => {
    renderWithProviders(<Devices />);
    fireEvent.click(await screen.findByTestId('mdm-update-kid-phone-0001'));
    await waitFor(() => expect(pushed).toEqual([{ device_keys: ['kid-phone-0001'] }]));
  });

  it('gates the remote wipe behind a confirmation', async () => {
    const confirm = vi.fn(() => false);
    vi.stubGlobal('confirm', confirm);
    renderWithProviders(<Devices />);
    fireEvent.click(await screen.findByTestId('mdm-wipe-kid-phone-0001'));
    expect(confirm).toHaveBeenCalled();
    expect(wiped).toEqual([]);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByTestId('mdm-wipe-kid-phone-0001'));
    await waitFor(() => expect(wiped).toEqual(['kid-phone-0001']));
    vi.unstubAllGlobals();
  });

  it('lists the shared School folder and its files', async () => {
    renderWithProviders(<Devices />);
    expect(await screen.findByTestId('mdm-folder-school')).toBeInTheDocument();
    expect(screen.getByText('worksheet.pdf')).toBeInTheDocument();
    expect(screen.getByText('No files in this folder yet.')).toBeInTheDocument();  // the empty Library
  });
});
