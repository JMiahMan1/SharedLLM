import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DuplicatesPanel from './DuplicatesPanel';
import * as dup from '../../lib/duplicates';
import type { Duplicates, DuplicateCopy } from '../../lib/duplicates';
import { bulkActions, combinedLabel, combinedStatus, defaultMain, matchLabel, undecided } from '../../lib/duplicates';
import type { Combined } from '../../lib/duplicates';
import { hiddenDevices, physicalDevices, type HardwareDevice } from '../../lib/hardware';

vi.mock('../../lib/duplicates', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getDuplicates: vi.fn(),
  hideCopiesOf: vi.fn(),
  showCopiesOf: vi.fn(),
  setDeviceHidden: vi.fn(),
  combineCopies: vi.fn(),
  combineAllLike: vi.fn(),
  uncombine: vi.fn(),
  swapMain: vi.fn(),
  clearRule: vi.fn(),
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

const combinable = (matterHidden = false, suggested: string | null = null): Duplicates => {
  const d = data(matterHidden);
  return { ...d, groups: [{ ...d.groups[0], can_combine: true, suggested_main: suggested }] };
};

const combined = (using: Combined['using'] = 'main'): Duplicates => ({
  groups: [{ id: 'm1', match: 'mac', name: 'Pantry Lamp', area: 'Pantry', domains: ['matter', 'wiz'],
    devices: [{ ...copy('m1', 'matter', 'Matter'), role: 'main' },
      { ...copy('w1', 'wiz', 'WiZ', true), hidden_by: 'combined', role: 'fallback' }],
    can_combine: false, suggested_main: 'matter',
    combined: { main: 'm1', fallback: 'w1', main_domain: 'matter', main_label: 'Matter', fallback_domain: 'wiz',
      fallback_label: 'WiZ', entities: [{ main: 'light.m1', fallback: 'light.w1' }],
      main_available: using === 'main', fallback_available: true, using } }],
  integrations: [],
  rules: [{ domains: ['matter', 'wiz'], main_domain: 'matter', main_label: 'Matter', fallback_domain: 'wiz',
    fallback_label: 'WiZ' }],
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

  it('offers the rule\'s main, else the copy that shows, and says what a combined device is doing', () => {
    expect(defaultMain(combinable().groups[0])).toBe('m1');
    expect(defaultMain(combinable(true).groups[0])).toBe('w1');
    expect(defaultMain(combinable(true, 'matter').groups[0])).toBe('m1');
    const c = combined().groups[0].combined!;
    expect(combinedLabel(c)).toBe('Main: Matter, fallback: WiZ');
    expect(combinedStatus(c)).toBe('Using Matter');
    expect(combinedStatus({ ...c, using: 'fallback' })).toBe('Matter is not answering, so WiZ is used');
    expect(combinedStatus({ ...c, using: null })).toBe('Neither copy is answering');
    expect(undecided(combined())).toBe(0);
  });

  it('says how the copies were matched', () => {
    const g = data().groups[0];
    expect(matchLabel(g)).toBe('Same name in Pantry');
    expect(matchLabel({ ...g, match: 'mac' })).toBe('Same network address');
    expect(matchLabel({ ...g, area: null })).toBe('Same name');
    expect(matchLabel({ ...g, match: 'serial' })).toBe('Same serial number');
  });

  it('keeps a hidden copy out of the tile and in the hidden list', () => {
    const view = (id: string, hidden: boolean, extra: Partial<HardwareDevice> = {}) => ({
      id, name: id, group_id: 'w1', hidden, status: 'online', area_id: 'pantry', area: 'Pantry',
      connections: [], abilities: [], ...extra,
    }) as unknown as HardwareDevice;
    const all = [view('m1', true), view('w1', false), view('solo', true, { group_id: 'solo' }),
      view('fb', true, { hidden_by: 'combined' })];
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

  it('combines a pair with the chosen main, or every pair like it', async () => {
    vi.mocked(dup.getDuplicates).mockResolvedValueOnce(combinable(true)).mockResolvedValue(combined());
    vi.mocked(dup.combineAllLike).mockResolvedValue(combined());
    renderPanel();
    const choice = await screen.findByTestId('combine-choice');
    // The WiZ copy shows today, so it is offered first; the person picks Matter.
    expect(within(choice).getByRole('radio', { name: 'WiZ as the main for Pantry Lamp' })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(within(choice).getByRole('radio', { name: 'Matter as the main for Pantry Lamp' }));
    expect(within(choice).getByTestId('combine-preview')).toHaveTextContent('Matter first, WiZ as the fallback.');
    fireEvent.click(within(choice).getByRole('button', { name: 'Combine all Matter and WiZ copies with Matter first' }));
    await waitFor(() => expect(dup.combineAllLike).toHaveBeenCalledWith('matter', 'wiz'));
    expect(await screen.findByTestId('combined')).toHaveTextContent('Main: Matter, fallback: WiZ');
  });

  it('combines one pair', async () => {
    vi.mocked(dup.getDuplicates).mockResolvedValue(combinable(false, 'matter'));
    vi.mocked(dup.combineCopies).mockResolvedValue(combined());
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Combine Pantry Lamp' }));
    await waitFor(() => expect(dup.combineCopies).toHaveBeenCalledWith('m1', 'w1'));
  });

  it('shows a combined device with swap and uncombine, and no hide buttons', async () => {
    vi.mocked(dup.getDuplicates).mockResolvedValue(combined('fallback'));
    vi.mocked(dup.swapMain).mockResolvedValue(combined());
    vi.mocked(dup.uncombine).mockResolvedValue(combinable());
    vi.mocked(dup.clearRule).mockResolvedValue({ ...combined(), rules: [] });
    renderPanel();
    const box = await screen.findByTestId('combined');
    expect(screen.getByTestId('combined-status')).toHaveTextContent('Matter is not answering, so WiZ is used');
    expect(screen.queryByRole('button', { name: /Hide the/ })).toBeNull();
    expect(screen.getAllByTestId('duplicate-copy').map((c) => c.textContent)).toEqual([
      expect.stringContaining('Main'), expect.stringContaining('Fallback')]);
    expect(screen.getByTestId('duplicates-rule')).toHaveTextContent('Household rule: prefer Matter, fall back to WiZ.');
    fireEvent.click(within(box).getByRole('button', { name: 'Make WiZ the main for Pantry Lamp' }));
    await waitFor(() => expect(dup.swapMain).toHaveBeenCalledWith('m1'));
    fireEvent.click(await screen.findByRole('button', { name: 'Uncombine Pantry Lamp' }));
    await waitFor(() => expect(dup.uncombine).toHaveBeenCalledWith('m1'));
  });

  it('says what went wrong when the hub cannot list them', async () => {
    vi.mocked(dup.getDuplicates).mockRejectedValue(new Error('down'));
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('That did not work');
  });
});
