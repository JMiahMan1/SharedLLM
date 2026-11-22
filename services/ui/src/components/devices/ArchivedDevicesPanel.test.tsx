import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ArchivedDevicesPanel from './ArchivedDevicesPanel';
import * as archive from '../../lib/deviceArchive';
import {
  daysUntil, deletionText, mergeArchived, settingsProblem, sourceText, type ArchivedItem,
} from '../../lib/deviceArchive';

vi.mock('../../lib/deviceArchive', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  getHubArchive: vi.fn(),
  getIdentityArchive: vi.fn(),
  saveArchiveSettings: vi.fn(),
  restoreArchived: vi.fn(),
  deleteArchived: vi.fn(),
}));

const SETTINGS = { enabled: true, archive_after_days: 90, delete_after_days: 365 };
const DAY = 86_400_000;
const iso = (offsetDays: number) => new Date(Date.now() + offsetDays * DAY).toISOString();

const plug: ArchivedItem = {
  kind: 'entry', id: 'e-plug', name: 'Garage plug', label: 'Tasmota', domain: 'tasmota', area: 'Garage',
  last_seen: iso(-120), archived_at: iso(-30), delete_on: iso(335), entities: 2,
};
const tablet: ArchivedItem = {
  kind: 'identity', id: 'tab-1', name: 'Old tablet', device_kind: 'tablet', owner: 'sam',
  last_seen: iso(-200), archived_at: iso(-100), delete_on: iso(265),
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><ArchivedDevicesPanel /></QueryClientProvider>);
}

describe('deviceArchive helpers', () => {
  it('merges both lists, newest archive first', () => {
    expect(mergeArchived([tablet], [plug]).map((i) => i.id)).toEqual(['e-plug', 'tab-1']);
    expect(mergeArchived(undefined, undefined)).toEqual([]);
  });

  it('says when a device will be deleted', () => {
    const now = Date.parse('2026-01-01T00:00:00Z');
    expect(daysUntil('2026-01-11T00:00:00Z', now)).toBe(10);
    expect(daysUntil('2025-12-01T00:00:00Z', now)).toBe(0);
    expect(deletionText({ ...plug, delete_on: '2026-01-11T00:00:00Z' }, now)).toMatch(/in 10 days\) unless it comes back$/);
    expect(deletionText({ ...plug, delete_on: '2026-01-02T00:00:00Z' }, now)).toMatch(/in 1 day\)/);
    expect(deletionText({ ...plug, delete_on: '2025-12-01T00:00:00Z' }, now)).toBe('Deleted at the next daily check');
    expect(deletionText({ ...plug, delete_on: null }, now)).toBe('Not scheduled for deletion');
  });

  it('names where a device comes from', () => {
    expect(sourceText(plug)).toBe('Tasmota · Garage');
    expect(sourceText(tablet)).toBe("Tablet, sam's");
  });

  it('checks the settings', () => {
    expect(settingsProblem({ archive_after_days: 90, delete_after_days: 365 })).toBeNull();
    expect(settingsProblem({ archive_after_days: 3, delete_after_days: 365 })).toMatch(/7 to 3650/);
    expect(settingsProblem({ archive_after_days: 90, delete_after_days: 10 })).toMatch(/30 to 3650/);
    expect(settingsProblem({ archive_after_days: Number.NaN, delete_after_days: 365 })).not.toBeNull();
  });
});

describe('ArchivedDevicesPanel', () => {
  beforeEach(() => {
    vi.mocked(archive.getHubArchive).mockReset().mockResolvedValue({ settings: SETTINGS, items: [plug], audit: [] });
    vi.mocked(archive.getIdentityArchive).mockReset().mockResolvedValue({ settings: SETTINGS, items: [tablet] });
    vi.mocked(archive.restoreArchived).mockReset().mockResolvedValue();
    vi.mocked(archive.deleteArchived).mockReset().mockResolvedValue();
    vi.mocked(archive.saveArchiveSettings).mockReset().mockResolvedValue();
  });

  it('lists hub and registered devices with last seen, archived since and deletion date', async () => {
    renderPanel();
    const rows = await screen.findAllByTestId('archived-device');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Garage plug');
    expect(rows[0]).toHaveTextContent('Tasmota · Garage');
    expect(within(rows[0]).getByTestId('archived-deletion')).toHaveTextContent(/unless it comes back/);
    expect(within(rows[1]).getByTestId('archived-last-seen')).toHaveTextContent('d ago');
    expect(screen.getByText('2 archived')).toBeInTheDocument();
  });

  it('restores one with a tap', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Restore Garage plug' }));
    await waitFor(() => expect(archive.restoreArchived).toHaveBeenCalledWith(plug, expect.anything()));
  });

  it('deletes now only after confirming', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Old tablet now' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Delete Old tablet?');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(archive.deleteArchived).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Delete Old tablet now' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(archive.deleteArchived).toHaveBeenCalledWith(tablet, expect.anything()));
  });

  it('saves the thresholds and refuses ones out of range', async () => {
    renderPanel();
    const input = await screen.findByLabelText('Archive after days');
    fireEvent.change(input, { target: { value: '3' } });
    expect(screen.getByRole('alert')).toHaveTextContent('7 to 3650');
    expect(screen.getByRole('button', { name: /Save/ })).toBeDisabled();
    fireEvent.change(input, { target: { value: '120' } });
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));
    await waitFor(() => expect(archive.saveArchiveSettings).toHaveBeenCalledWith(
      { archive_after_days: 120, delete_after_days: 365, enabled: true }));
  });

  it('says so when there is nothing archived, and when one source fails', async () => {
    vi.mocked(archive.getHubArchive).mockResolvedValue({ settings: SETTINGS, items: [], audit: [] });
    vi.mocked(archive.getIdentityArchive).mockRejectedValue(new Error('down'));
    renderPanel();
    expect(await screen.findByTestId('archived-empty')).toHaveTextContent('No archived devices.');
    expect(await screen.findByText("Jarvis's registered devices could not be read.")).toBeInTheDocument();
  });
});
