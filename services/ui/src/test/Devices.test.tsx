import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import toast from 'react-hot-toast';
import Devices from '../pages/Devices';
import { renderWithProviders } from './render';
import { server } from './setup';
import { confirmDialog } from '../components/ui/confirmDialog';

// Destructive actions draw the app's own dialog now, not the browser's.
vi.mock('../components/ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
const confirmMock = vi.mocked(confirmDialog);

vi.mock('react-hot-toast', () => {
  const toast = vi.fn();
  return { default: Object.assign(toast, { success: vi.fn(), error: vi.fn() }) };
});

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

const unprotectedDevice = {
  devices: [
    {
      ...inventory.devices[0],
      capabilities: { guard: { deviceOwner: false, guardEnabled: false, webFilterActive: false, reportedAt: '2026-10-25T10:00:00Z' } },
    },
  ],
  members: inventory.members,
  generated_at: inventory.generated_at,
};

// Not a device owner, but the screen guard is on: the full-screen Accessibility
// overlay lock really covers this phone, so the lock is real.
const guardedDevice = {
  devices: [
    {
      ...inventory.devices[0],
      capabilities: { guard: { deviceOwner: false, guardEnabled: true, webFilterActive: true, reportedAt: '2026-10-25T10:00:00Z' } },
    },
  ],
  members: inventory.members,
  generated_at: inventory.generated_at,
};

const folders = {
  folders: [
    { folder: 'school', files: [{ name: 'worksheet.pdf', size: 10, sha256: 'x', content_type: 'application/pdf', uploaded_by: 'dad', uploaded_at: '2026-10-25T09:00:00Z' }], pushed_to: ['kid'] },
    { folder: 'library', files: [], pushed_to: [] },
  ],
};

let pushed: Array<Record<string, unknown>> = [];
let wiped: string[] = [];

const libraryBooks = {
  configured: true,
  root: '/Books/Text',
  books: [
    { name: 'Pilgrim - Bunyan.epub', path: '/Books/Text/Bunyan/Pilgrim (1)/Pilgrim - Bunyan.epub', format: 'epub', size: 2048 },
  ],
  count: 1,
};

const auditEvents = {
  events: [
    { at: '2026-10-25T11:00:00Z', action: 'device_lock', actor: 'dad', target: 'kid', detail: { self: false } },
    { at: '2026-10-25T10:30:00Z', action: 'file_upload', actor: 'dad', target: 'school/worksheet.pdf', detail: { size: 10 } },
  ],
};

beforeEach(() => {
  pushed = [];
  wiped = [];
  vi.clearAllMocks();
  server.use(
    http.get('/api/mdm/devices', () => HttpResponse.json(inventory)),
    http.get('/api/mdm/files', () => HttpResponse.json(folders)),
    http.get('/api/mdm/files/for-me', () => HttpResponse.json({ folders: [] })),
    http.get('/api/mdm/library/books', () => HttpResponse.json(libraryBooks)),
    http.get('/api/mdm/audit', () => HttpResponse.json(auditEvents)),
    http.get('/api/mdm/files/school/worksheet.pdf', () => new HttpResponse(new Blob(['worksheet']), { headers: { 'Content-Type': 'application/pdf' } })),
    http.post('/api/screen-time/:username/pause', () => HttpResponse.json({ rules: {} })),
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

  it('says "Locked" on a device-owner phone, where the lock is real', async () => {
    renderWithProviders(<Devices />);
    fireEvent.click(await screen.findByTestId('mdm-lock-kid-phone-0001'));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Locked'));
    expect(toast).not.toHaveBeenCalledWith(expect.stringContaining('Not locked'), expect.anything());
  });

  it('says "Locked" on a phone with the screen guard on, where the overlay lock is real', async () => {
    server.use(http.get('/api/mdm/devices', () => HttpResponse.json(guardedDevice)));
    renderWithProviders(<Devices />);
    fireEvent.click(await screen.findByTestId('mdm-lock-kid-phone-0001'));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Locked'));
    expect(toast).not.toHaveBeenCalledWith(expect.stringContaining('Not locked'), expect.anything());
  });

  it('never claims a real lock when the phone has no device owner and no screen guard', async () => {
    server.use(http.get('/api/mdm/devices', () => HttpResponse.json(unprotectedDevice)));
    renderWithProviders(<Devices />);
    fireEvent.click(await screen.findByTestId('mdm-lock-kid-phone-0001'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining('Not locked'), expect.anything()));
    expect(toast.success).not.toHaveBeenCalledWith('Locked');
  });

  it('gates the remote wipe behind a confirmation', async () => {
    confirmMock.mockResolvedValue(false);
    renderWithProviders(<Devices />);
    fireEvent.click(await screen.findByTestId('mdm-wipe-kid-phone-0001'));
    expect(confirmMock).toHaveBeenCalled();
    expect(wiped).toEqual([]);
    confirmMock.mockResolvedValue(true);
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

  it('shows the audit log of parent actions', async () => {
    renderWithProviders(<Devices />);
    expect(await screen.findByTestId('mdm-audit')).toBeInTheDocument();
    expect(await screen.findByText('locked a device')).toBeInTheDocument();
    expect(await screen.findByText('uploaded a shared file')).toBeInTheDocument();
  });

  it('shows the read-only family library beside the Library folder', async () => {
    renderWithProviders(<Devices />);
    expect(await screen.findByTestId('mdm-library-books')).toBeInTheDocument();
    expect(await screen.findByText('Pilgrim - Bunyan.epub')).toBeInTheDocument();
  });

  it('distinguishes tablets and ESPHome watches with platform-aware controls', async () => {
    const esphomeCalls: Array<Record<string, unknown>> = [];
    server.use(
      http.get('/api/mdm/devices', () =>
        HttpResponse.json({
          devices: [
            {
              device_key: 'kid-tab-0001',
              kind: 'tablet',
              platform: 'android',
              label: "Madelyn's Galaxy Tab",
              manufacturer: 'samsung',
              model: 'SM-P620',
              os_version: '16',
              os_build: 'UP1A',
              app_version: '1.9.0',
              app_build: '493',
              last_seen_at: '2026-10-25T10:00:00Z',
              first_seen_at: '2026-10-01T10:00:00Z',
              last_ip_address: '192.168.2.42',
              battery: { at: '2026-10-25T10:00:00Z', pct: 84, usb: false },
              capabilities: { guard: { deviceOwner: true, guardEnabled: true, webFilterActive: true } },
              owner_username: 'madelyn',
              owner_display_name: 'Madelyn',
            },
            {
              device_key: 'esphome:744dbd2c9728',
              kind: 'watch',
              platform: 'esphome',
              registered_by: 'paired',
              label: 'Jarvis Watch',
              manufacturer: null,
              model: null,
              os_version: null,
              os_build: null,
              app_version: '1.4.6',
              app_build: null,
              esphome_version: '2026.9.0',
              hardware: 'esp32-s3-devkitc-1',
              last_seen_at: '2026-10-25T10:05:00Z',
              first_seen_at: '2026-10-01T10:00:00Z',
              last_ip_address: '192.168.2.105',
              battery: { at: '2026-10-25T10:05:00Z', pct: 72, usb: false },
              capabilities: {},
              owner_username: 'jeremiah',
              owner_display_name: 'Jeremiah',
            },
          ],
          members: [
            { username: 'madelyn', display_name: 'Madelyn', device_count: 1, readable: true, reason: null },
            { username: 'jeremiah', display_name: 'Jeremiah', device_count: 1, readable: true, reason: null },
          ],
          generated_at: '2026-10-25T10:05:00Z',
        }),
      ),
      http.post('/execute/esphome', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        esphomeCalls.push(body);
        if (body.action === 'list') {
          return HttpResponse.json({
            status: 'SUCCESS',
            service: 'execution',
            message: 'Listed 2 entities',
            detail: {
              device_info: {
                name: 'jarvis-watch',
                friendly_name: 'Jarvis Watch',
                model: 'esp32-s3-devkitc-1',
                esphome_version: '2026.9.0',
              },
              entities: [
                { domain: 'button', object_id: 'find_watch_buzzer', name: 'Find Watch Buzzer' },
                { domain: 'sensor', object_id: 'watch_battery', name: 'Watch Battery' },
              ],
            },
          });
        }
        return HttpResponse.json({
          status: 'SUCCESS',
          service: 'execution',
          message: 'Configured Jarvis Watch',
        });
      }),
    );

    renderWithProviders(<Devices />);

    // Tablet shows Android · Tablet badge and tablet-specific MDM controls
    expect(await screen.findByTestId('mdm-device-badge-kid-tab-0001')).toHaveTextContent('Android · Tablet · kid-tab-0001');
    expect(screen.getByTestId('mdm-kind-toggle-kid-tab-0001')).toHaveTextContent('Mark as phone');
    expect(screen.getByTestId('mdm-guard-kid-tab-0001')).toBeInTheDocument();

    // ESPHome watch shows ESPHome · Watch badge, hardware/firmware facts, and ESPHome native controls (no Android guard/wipe)
    expect(screen.getByTestId('mdm-device-badge-esphome:744dbd2c9728')).toHaveTextContent('ESPHome · Watch · esphome:744dbd2c9728');
    expect(screen.getByTestId('mdm-esphome-esphome:744dbd2c9728')).toHaveTextContent('ESPHome companion device');
    expect(screen.getByText('esp32-s3-devkitc-1')).toBeInTheDocument();
    expect(screen.getByText('2026.9.0')).toBeInTheDocument();
    expect(screen.queryByTestId('mdm-guard-esphome:744dbd2c9728')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mdm-wipe-esphome:744dbd2c9728')).not.toBeInTheDocument();

    // Probe ESPHome entities and trigger a button entity
    fireEvent.click(screen.getByTestId('mdm-esphome-probe-esphome:744dbd2c9728'));
    expect(await screen.findByTestId('mdm-esphome-entities-esphome:744dbd2c9728')).toHaveTextContent('Find Watch Buzzer');
    fireEvent.click(screen.getByTestId('mdm-esphome-call-esphome:744dbd2c9728-find_watch_buzzer'));
    await waitFor(() =>
      expect(esphomeCalls).toEqual([
        { action: 'list', device: 'Jarvis Watch' },
        { action: 'call', device: 'Jarvis Watch', entity: 'find_watch_buzzer', params: null },
      ]),
    );

    // Sync Jarvis key & URL
    fireEvent.click(screen.getByTestId('mdm-esphome-sync-esphome:744dbd2c9728'));
    await waitFor(() =>
      expect(esphomeCalls[2]).toEqual({
        action: 'configure_jarvis',
        device: 'Jarvis Watch',
        params: null,
      }),
    );
  });
});

describe('Devices page for a child', () => {
  const childMe = http.get('/api/users/me', () => HttpResponse.json({ id: 2, username: 'kid', is_admin: false }));
  const forMe = (folders: unknown) => http.get('/api/mdm/files/for-me', () => HttpResponse.json({ folders }));

  it('lists only the folders pushed to the child, plus the family library', async () => {
    server.use(
      childMe,
      forMe([
        { folder: 'school', files: [{ name: 'worksheet.pdf', size: 10, sha256: 'x', content_type: 'application/pdf', uploaded_by: 'dad', uploaded_at: '2026-10-25T09:00:00Z' }] },
        { folder: 'library', files: [] },
      ]),
    );
    renderWithProviders(<Devices />);
    expect(await screen.findByTestId('mdm-for-me-school')).toHaveTextContent('worksheet.pdf');
    expect(await screen.findByText('Pilgrim - Bunyan.epub')).toBeInTheDocument();
    // A child never sees the parent's device inventory.
    expect(screen.queryByTestId('mdm-device-kid-phone-0001')).not.toBeInTheDocument();
  });

  it('says so when nothing has been pushed yet', async () => {
    server.use(childMe, forMe([]));
    renderWithProviders(<Devices />);
    expect(await screen.findByTestId('mdm-for-me-empty')).toHaveTextContent('No shared folders have been pushed to you yet.');
  });

  it('opens a shared file in the in-app viewer', async () => {
    const realCreate = URL.createObjectURL;
    URL.createObjectURL = vi.fn(() => 'blob:mock');
    URL.revokeObjectURL = vi.fn();
    try {
      server.use(
        childMe,
        forMe([
          { folder: 'school', files: [{ name: 'worksheet.pdf', size: 10, sha256: 'x', content_type: 'application/pdf', uploaded_by: 'dad', uploaded_at: '2026-10-25T09:00:00Z' }] },
        ]),
      );
      renderWithProviders(<Devices />);
      fireEvent.click(await screen.findByRole('button', { name: 'worksheet.pdf' }));
      expect(await screen.findByRole('dialog', { name: 'worksheet.pdf preview' })).toBeInTheDocument();
    } finally {
      URL.createObjectURL = realCreate;
    }
  });
});
