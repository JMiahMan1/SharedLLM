import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import NewDevicesPanel from './NewDevicesPanel';
import AdoptDeviceDialog from './AdoptDeviceDialog';
import * as discovered from '../../lib/discoveredDevices';
import type { DiscoveredDevice } from '../../lib/discoveredDevices';

vi.mock('../../lib/discoveredDevices', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getDiscovered: vi.fn(),
  getManualFlows: vi.fn(),
  getEntries: vi.fn().mockResolvedValue({ areas: [], entries: [] }),
  updateAddress: vi.fn(),
}));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const roku: DiscoveredDevice = {
  id: 'roku:x1', integration: 'roku', label: 'Roku', name: 'Den TV', model: null, manufacturer: null,
  host: '192.0.2.96', mac: null, source: 'ssdp', first_seen: null, last_seen: null, known: true, entry_id: 'e1',
  entry_title: 'Den TV', ignored: false, new: false, adoptable: false, unsupported: null, needs: [],
  moved: { entry_id: 'e1', entry_title: 'Den TV', old_host: '192.0.2.66', new_host: '192.0.2.96' },
};

const wrap = (ui: React.ReactNode) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>,
);

describe('New devices: moved devices and adding by hand', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(discovered.getDiscovered).mockResolvedValue({ devices: [roku] });
  });

  it('offers a set-up device at a new address as an update to its entry', async () => {
    vi.mocked(discovered.updateAddress).mockResolvedValue({ status: 'updated', new_host: '192.0.2.96', old_host: '192.0.2.66' });
    wrap(<NewDevicesPanel />);
    const row = await screen.findByTestId('moved-device');
    expect(row).toHaveTextContent('192.0.2.66 → 192.0.2.96');
    expect(screen.queryAllByTestId('new-device')).toHaveLength(0);
    fireEvent.click(within(row).getByRole('button', { name: 'Use the new address for Den TV' }));
    await waitFor(() => expect(discovered.updateAddress).toHaveBeenCalledWith('roku:x1'));
  });

  it('groups what can be added by kind and leaves out a one-per-hub one already set up', async () => {
    vi.mocked(discovered.getManualFlows).mockResolvedValue([
      { domain: 'roku', label: 'Roku', kind: 'local', fields: [] },
      { domain: 'roborock', label: 'Roborock account', kind: 'cloud', fields: [] },
      { domain: 'sun', label: 'Sun', kind: 'helper', single: true, configured: true, fields: [] },
      { domain: 'local_todo', label: 'To-do list', kind: 'helper', fields: [] },
    ]);
    wrap(<NewDevicesPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /Add by address/ }));
    const picker = await screen.findByTestId('add-by-address');
    await within(picker).findByText('Cloud accounts');
    expect(within(picker).getByText('On the network')).toBeInTheDocument();
    expect(within(picker).queryByRole('button', { name: 'Sun' })).toBeNull();
    expect(within(picker).getByRole('button', { name: 'To-do list' })).toBeInTheDocument();
  });

  it('shows choices the hub sends as a select', async () => {
    const submit = vi.fn().mockResolvedValue({ status: 'adopted', name: 'Lights' });
    wrap(<AdoptDeviceDialog title="Add" onClose={() => {}} onDone={() => {}} onSubmit={submit}
      needs={[{ key: 'entity_id', label: 'Switch', type: 'select', options: [{ value: 'switch.example', label: 'Example plug' }] }]} />);
    const select = screen.getByLabelText('Switch');
    fireEvent.change(select, { target: { value: 'switch.example' } });
    fireEvent.click(screen.getByRole('button', { name: /^Add$/ }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ name: undefined, areaId: undefined, input: { entity_id: 'switch.example' } }));
  });
});
