import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { FirmwarePanel } from './FirmwarePanel';
import type { FirmwareReport, FirmwareRow } from '../../lib/firmware';

const mockApi = vi.hoisted(() => ({
  getFirmware: vi.fn(),
  installFirmware: vi.fn(),
  upgradeDeviceFirmware: vi.fn(),
}));

const mockConfirm = vi.hoisted(() => vi.fn().mockResolvedValue(true));

vi.mock('../../services/api', () => ({ api: mockApi }));
vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'admin', is_admin: true }, isAuthenticated: true }),
}));
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: mockConfirm }));
vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
  toast: { success: vi.fn(), error: vi.fn() },
}));

const esphome: FirmwareRow = {
  id: 'update.kitchen_firmware',
  entity_id: 'update.kitchen_firmware',
  device_id: 'dev-kitchen',
  device: 'Kitchen panel',
  name: 'Firmware',
  integration: 'esphome',
  model: 'ESP32-S3',
  installed_version: '2026.10.17',
  latest_version: '2026.11.18',
  available: true,
  installable: true,
  in_progress: false,
  release_url: null,
  source: 'entity',
  action: 'install',
};

const tasmota: FirmwareRow = {
  id: 'dev-shed',
  entity_id: null,
  device_id: 'dev-shed',
  device: 'Shed relay',
  name: 'Shed relay',
  integration: 'tasmota',
  model: 'ESP8266EX',
  installed_version: '15.6.0',
  latest_version: null,
  available: false,
  upgradable: true,
  installable: false,
  in_progress: false,
  release_url: null,
  source: 'device',
  action: 'upgrade',
};

const report = (devices: FirmwareRow[], installing = 0): FirmwareReport => ({ devices, installing });

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FirmwarePanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('FirmwarePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConfirm.mockResolvedValue(true);
    mockApi.installFirmware.mockResolvedValue({ started: true });
    mockApi.upgradeDeviceFirmware.mockResolvedValue({ reply: { Upgrade: '1' } });
  });

  it('lists every device with the version it runs and the one offered', async () => {
    mockApi.getFirmware.mockResolvedValue(report([esphome, tasmota]));
    renderPanel();

    expect(await screen.findByText('Kitchen panel')).toBeInTheDocument();
    expect(screen.getByText('2026.10.17 to 2026.11.18')).toBeInTheDocument();
    expect(screen.getByText('Update available')).toBeInTheDocument();
    expect(screen.getByText('Shed relay')).toBeInTheDocument();
    expect(screen.getByText("15.6.0, updated from the device's own update address")).toBeInTheDocument();
    expect(screen.getByText('Updated from its own address')).toBeInTheDocument();
  });

  it('installs the offered version for the row that was tapped', async () => {
    mockApi.getFirmware.mockResolvedValue(report([esphome]));
    renderPanel();

    await userEvent.click(await screen.findByTestId('firmware-install-update.kitchen_firmware'));

    await waitFor(() => expect(mockApi.installFirmware).toHaveBeenCalledWith('update.kitchen_firmware'));
    expect(mockConfirm).toHaveBeenCalled();
  });

  it('does nothing when the confirm is answered no', async () => {
    mockConfirm.mockResolvedValue(false);
    mockApi.getFirmware.mockResolvedValue(report([esphome]));
    renderPanel();

    await userEvent.click(await screen.findByTestId('firmware-install-update.kitchen_firmware'));

    expect(mockConfirm).toHaveBeenCalled();
    expect(mockApi.installFirmware).not.toHaveBeenCalled();
  });

  it('asks a device that fetches its own image to update', async () => {
    mockApi.getFirmware.mockResolvedValue(report([tasmota]));
    renderPanel();

    await userEvent.click(await screen.findByTestId('firmware-upgrade-dev-shed'));

    await waitFor(() => expect(mockApi.upgradeDeviceFirmware).toHaveBeenCalledWith('dev-shed'));
  });

  it('updates every installable offer once, one after another', async () => {
    mockApi.getFirmware.mockResolvedValue(report([esphome, tasmota]));
    renderPanel();

    await userEvent.click(await screen.findByTestId('firmware-update-all'));

    await waitFor(() => expect(mockApi.installFirmware).toHaveBeenCalledTimes(1));
    expect(mockApi.installFirmware).toHaveBeenCalledWith('update.kitchen_firmware');
    expect(mockApi.upgradeDeviceFirmware).not.toHaveBeenCalled();
  });

  it('shows an install in progress with its percentage', async () => {
    mockApi.getFirmware.mockResolvedValue(
      report([{ ...esphome, available: true, in_progress: true, progress: 42 }], 1),
    );
    renderPanel();

    expect(await screen.findByText('Installing 42%')).toBeInTheDocument();
  });

  it('says plainly when nothing reports a version', async () => {
    mockApi.getFirmware.mockResolvedValue(report([]));
    renderPanel();

    expect(await screen.findByText(/No device reports a version yet/)).toBeInTheDocument();
  });
});
