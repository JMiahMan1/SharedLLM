import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DuplicatesPanel from './DuplicatesPanel';
import * as dup from '../../lib/duplicates';
import type { Duplicates, DuplicateCopy } from '../../lib/duplicates';
import { bulkActions, matchLabel, undecided } from '../../lib/duplicates';
import { hiddenDevices, physicalDevices, type HardwareDevice } from '../../lib/hardware';

vi.mock('../../lib/duplicates', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getDuplicates: vi.fn(),
  hideCopiesOf: vi.fn(),
  showCopiesOf: vi.fn(),
  setDeviceHidden: vi.fn(),
}));

const copy = (id: string, domain: string, label: string, hidden = false): DuplicateCopy => ({
  id, name: 'Pantry Lamp', manufacturer: null, model: null, domain, label, entry_id: `e-${domain}`,
  entry_title: label, area_id: 'pantry', area: 'Pantry', entity_ids: [`light.${id}`], hidden,
  hidden_by: hidden ? 'user' : null, last_updated: null,
});

const data = (matterHidden = false): Duplicates => ({
  groups: [{ id: 'm1', match: 'name', name: 'Pantry Lamp', area: 'Pantry', domains: ['matter', 'wiz'],
    devices: [copy('m1', 'matter', 'Matter', matterHidden), copy('w1', 'wiz', 'WiZ')] }],
  integrations: [
    { domain: 'matter', label: 'Matter', groups: 1, devices: 1, hidden: matterHidden ? 1 : 0 },
    { domain: 'wiz', label: 'WiZ', groups: 1, devices: 1, hidden: 0 },
  ],
});

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><DuplicatesPanel /></QueryClientProvider>);
}

describe('duplicates helpers', () => {
  it('offers hide while a copy shows and show once one is hidden', () => {
    expect(bulkActions(data())).toEqual([
      { domain: 'matter', label: 'Matter', canHide: true, canShow: false },
      { domain: 'wiz', label: 'WiZ', canHide: true, canShow: false },
    ]);
    expect(bulkActions(data(true))[0]).toEqual({ domain: 'matter', label: 'Matter', canHide: false, canShow: true });
    expect(undecided(data())).toBe(1);
    expect(undecided(data(true))).toBe(0);
  });

  it('says how the copies were matched', () => {
    const g = data().groups[0];
    expect(matchLabel(g)).toBe('Same name in Pantry');
    expect(matchLabel({ ...g, match: 'mac' })).toBe('Same network address');
    expect(matchLabel({ ...g, area: null })).toBe('Same name');
  });

  it('keeps a hidden copy out of the tile and in the hidden list', () => {
    const view = (id: string, hidden: boolean, extra: Partial<HardwareDevice> = {}) => ({
      id, name: id, group_id: 'w1', hidden, status: 'online', area_id: 'pantry', area: 'Pantry',
      connections: [], abilities: [], ...extra,
    }) as unknown as HardwareDevice;
    const all = [view('m1', true), view('w1', false), view('solo', true, { group_id: 'solo' })];
    const { devices } = physicalDevices(all);
    expect(devices.map((d) => [d.id, d.views.map((v) => v.id)])).toEqual([['w1', ['w1']]]);
    expect(hiddenDevices(all).map((d) => d.id)).toEqual(['m1', 'solo']);
  });
});

describe('DuplicatesPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows nothing when no device is in the hub twice', async () => {
    vi.mocked(dup.getDuplicates).mockResolvedValue({ groups: [], integrations: [] });
    const { container } = renderPanel();
    await waitFor(() => expect(dup.getDuplicates).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('lists each copy with its integration and hides one', async () => {
    vi.mocked(dup.getDuplicates).mockResolvedValue(data());
    vi.mocked(dup.setDeviceHidden).mockResolvedValue({ id: 'm1', hidden: true });
    renderPanel();
    const group = await screen.findByTestId('duplicate-group');
    expect(within(group).getByText('Same name in Pantry')).toBeInTheDocument();
    const copies = within(group).getAllByTestId('duplicate-copy');
    expect(copies.map((c) => c.textContent)).toEqual([expect.stringContaining('Matter'), expect.stringContaining('WiZ')]);
    fireEvent.click(screen.getByRole('button', { name: 'Hide the Matter copy of Pantry Lamp' }));
    await waitFor(() => expect(dup.setDeviceHidden).toHaveBeenCalledWith('m1', true));
  });

  it('hides every copy of one integration and then offers to show them', async () => {
    vi.mocked(dup.getDuplicates).mockResolvedValueOnce(data()).mockResolvedValue(data(true));
    vi.mocked(dup.hideCopiesOf).mockResolvedValue({ changed: ['m1'], ...data(true) });
    vi.mocked(dup.showCopiesOf).mockResolvedValue({ changed: ['m1'], ...data() });
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /Hide all Matter copies/ }));
    await waitFor(() => expect(dup.hideCopiesOf).toHaveBeenCalledWith('matter'));
    fireEvent.click(await screen.findByRole('button', { name: /Show all Matter copies/ }));
    await waitFor(() => expect(dup.showCopiesOf).toHaveBeenCalledWith('matter'));
    expect(screen.getByRole('button', { name: 'Show the Matter copy of Pantry Lamp' })).toBeInTheDocument();
  });

  it('says what went wrong when the hub cannot list them', async () => {
    vi.mocked(dup.getDuplicates).mockRejectedValue(new Error('down'));
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('That did not work');
  });
});
