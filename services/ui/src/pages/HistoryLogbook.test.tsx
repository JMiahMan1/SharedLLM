import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import * as dashboards from '../lib/dashboards';
import { logbookByDay, logbookPhrase, type LogbookEntry } from '../lib/dashboards';
import History from './History';

vi.mock('../lib/dashboards', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, getRooms: vi.fn(), getEntities: vi.fn(), getHistory: vi.fn(), getStatistics: vi.fn(), getLogbook: vi.fn() };
});
vi.mock('../services/api', () => ({ api: {}, apiClient: {} }));
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'kid', is_admin: false } }) }));

const at = (h: number) => new Date(2026, 10, 14, h, 5).toISOString();

function wrap(path: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><History /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the logbook tab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(dashboards.getRooms).mockResolvedValue({ floors: [], unassigned: [], areas: [
      { area_id: 'den', name: 'Den', floor_id: null, icon: null, entities: [
        { entity_id: 'light.den_lamp', domain: 'light', name: 'Lamp', state: 'on', attributes: {}, device_class: null, unit: null,
          icon: null, area_id: 'den', device_id: null, last_changed: at(1) }] }] });
    vi.mocked(dashboards.getEntities).mockResolvedValue([]);
    vi.mocked(dashboards.getLogbook).mockResolvedValue({ truncated: true, entries: [
      { when: at(9), entity_id: 'light.den_lamp', name: 'Lamp', state: 'on' },
      { when: at(8), entity_id: 'sensor.den_temp', name: 'Den temperature', state: '71.5' },
    ] });
  });

  it('shows what changed, by day, and narrows to a room', async () => {
    wrap('/history?view=logbook');
    expect(await screen.findByText('Lamp')).toBeTruthy();
    expect(screen.getByText('turned on')).toBeTruthy();
    expect(screen.getByText('changed to 71.5')).toBeTruthy();
    expect(screen.getByText(/Showing the newest 2/)).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Logbook' }).getAttribute('aria-selected')).toBe('true');
    expect(dashboards.getHistory).not.toHaveBeenCalled();
    fireEvent.change(await screen.findByRole('combobox', { name: 'Room' }), { target: { value: 'den' } });
    await waitFor(() => expect(dashboards.getLogbook).toHaveBeenLastCalledWith(expect.objectContaining({ areaId: 'den' })));
  });

  it('passes the picked devices and the range along', async () => {
    wrap('/history?view=logbook&e=light.den_lamp&r=7d');
    await screen.findByText('Lamp');
    const call = vi.mocked(dashboards.getLogbook).mock.calls[0][0];
    expect(call.entityIds).toEqual(['light.den_lamp']);
    expect(call.end.getTime() - call.start.getTime()).toBe(7 * 24 * 3600 * 1000);
  });

  it('switches between charts and the logbook', async () => {
    wrap('/history');
    expect(screen.getByText('Pick something to chart')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'Logbook' }));
    expect(await screen.findByTestId('logbook')).toBeTruthy();
  });
});

describe('logbook helpers', () => {
  it('groups by local day, today and yesterday by name', () => {
    const now = new Date(2026, 10, 14, 12);
    const e = (when: Date): LogbookEntry => ({ when: when.toISOString(), entity_id: 'light.a', name: 'A', state: 'on' });
    const days = logbookByDay([e(new Date(2026, 10, 14, 9)), e(new Date(2026, 10, 14, 1)), e(new Date(2026, 10, 13, 23)),
      e(new Date(2026, 10, 10, 8))], now);
    expect(days.map((d) => [d.day, d.entries.length])).toEqual([['Today', 2], ['Yesterday', 1], [days[2].day, 1]]);
    expect(days[2].day).not.toMatch(/Today|Yesterday/);
  });

  it('words a change', () => {
    expect(logbookPhrase('off')).toBe('turned off');
    expect(logbookPhrase('not_home')).toBe('changed to not home');
    expect(logbookPhrase('unavailable')).toBe('became unavailable');
  });
});
