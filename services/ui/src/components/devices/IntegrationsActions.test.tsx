import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import IntegrationsPanel from './IntegrationsPanel';
import * as discovered from '../../lib/discoveredDevices';
import type { HubEntry } from '../../lib/discoveredDevices';

vi.mock('../../lib/discoveredDevices', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getEntries: vi.fn(),
  getBuilderLink: vi.fn().mockResolvedValue({ configured: false }),
  reloadEntry: vi.fn(),
  setEntryDisabled: vi.fn(),
  syncEntry: vi.fn(),
}));
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const entry = (over: Partial<HubEntry>): HubEntry => ({
  entry_id: 'e1', domain: 'music_assistant', label: 'Music Assistant', title: 'Music server', state: 'loaded',
  reason: null, source: 'user', disabled_by: null, devices: [], entities: 0, reconfigure: [], unpairs: false,
  shared: false, syncs: true, ...over,
});

const wrap = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <IntegrationsPanel />
  </QueryClientProvider>,
);

describe('Integrations: reload, disable and sync', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(discovered.getEntries).mockResolvedValue({ areas: [], entries: [
      entry({}), entry({ entry_id: 'e2', domain: 'wiz', label: 'WiZ', title: 'Porch lamp', syncs: false,
        disabled_by: 'user', state: 'not_loaded' }),
    ] });
    vi.mocked(discovered.reloadEntry).mockResolvedValue({ entry_id: 'e1', state: 'reloading' });
    vi.mocked(discovered.syncEntry).mockResolvedValue({ status: 'synced', entities: ['media_player.x'], devices: [] });
    vi.mocked(discovered.setEntryDisabled).mockResolvedValue(entry({}));
  });

  it('offers each action where it applies and calls the hub', async () => {
    wrap();
    const rows = await screen.findAllByTestId('integration');
    const ma = rows.find((r) => r.textContent?.includes('Music server'))!;
    const wiz = rows.find((r) => r.textContent?.includes('Porch lamp'))!;
    expect(within(wiz).queryByRole('button', { name: 'Reload Porch lamp' })).toBeNull();
    expect(within(wiz).queryByRole('button', { name: /Sync devices/ })).toBeNull();
    expect(within(wiz).getByText('disabled')).toBeInTheDocument();
    fireEvent.click(within(ma).getByRole('button', { name: 'Reload Music server' }));
    await waitFor(() => expect(discovered.reloadEntry).toHaveBeenCalledWith('e1'));
    fireEvent.click(within(ma).getByRole('button', { name: 'Sync devices of Music server' }));
    await waitFor(() => expect(discovered.syncEntry).toHaveBeenCalledWith('e1'));
    fireEvent.click(within(wiz).getByRole('button', { name: 'Enable Porch lamp' }));
    await waitFor(() => expect(discovered.setEntryDisabled).toHaveBeenCalledWith('e2', false));
    fireEvent.click(within(ma).getByRole('button', { name: 'Disable Music server' }));
    await waitFor(() => expect(discovered.setEntryDisabled).toHaveBeenCalledWith('e1', true));
  });
});
