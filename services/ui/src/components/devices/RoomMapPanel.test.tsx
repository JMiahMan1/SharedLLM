import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError, AxiosHeaders } from 'axios';
import RoomMapPanel from './RoomMapPanel';
import * as roomMap from '../../lib/roomMap';
import * as keepAwake from '../../lib/keepAwake';
import { confirmDialog } from '../ui/confirmDialog';

const UUID = '00000000-0000-4000-8000-0000000000aa';
const platform = vi.hoisted(() => ({ name: 'android' }));
const beaconStatus = vi.hoisted(() => vi.fn());

vi.mock('@capacitor/core', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, Capacitor: { ...(real.Capacitor as object), getPlatform: () => platform.name } };
});
vi.mock('../../plugins/bleBeacon', () => ({ default: { getStatus: beaconStatus } }));
vi.mock('../../lib/roomMap', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return {
    ...real,
    getRoomMap: vi.fn(),
    startMapSession: vi.fn(),
    getMapSession: vi.fn(),
    cancelMapSession: vi.fn(async () => undefined),
    clearMappedRoom: vi.fn(async () => undefined),
    clearWholeMap: vi.fn(async () => undefined),
  };
});
vi.mock('../../lib/keepAwake', () => ({ holdScreenOn: vi.fn() }));
vi.mock('../../lib/discoveredDevices', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, getEntries: vi.fn(async () => ({ entries: [], areas: [{ area_id: 'den', name: 'Den' }, { area_id: 'loft', name: 'Loft' }] })) };
});
vi.mock('../../lib/phones', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return { ...real, getPhones: vi.fn(async () => ({ phones: [
    { entry_id: 'p1', name: "Alex's Phone", sensors: [{ entity_id: 'sensor.alex_s_phone_room', domain: 'sensor' }] },
  ], settings: {} })) };
});
vi.mock('../ui/confirmDialog', () => ({ confirmDialog: vi.fn(async () => true) }));
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

const MAP = {
  method: 'fingerprint', scanners: [], overall_accuracy: 0.78, stale_reason: null,
  confusion: { den: { den: 50, loft: 6 }, loft: { loft: 30 } },
  areas: [{ area_id: 'den', name: 'Den', samples: 56, sessions: 1, last_recorded: new Date().toISOString(), accuracy: 0.89 }],
};

const wrap = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <RoomMapPanel />
  </QueryClientProvider>,
);

describe('RoomMapPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    platform.name = 'android';
    beaconStatus.mockResolvedValue({ enabled: true, state: 'advertising', uuid: UUID, major: 1, minor: 2, txPowerLevel: 'low',
      measuredPower: -59, permissionGranted: true });
    vi.mocked(roomMap.getRoomMap).mockResolvedValue(MAP as roomMap.RoomMap);
    vi.mocked(keepAwake.holdScreenOn).mockResolvedValue({ how: 'wake-lock', release: vi.fn(async () => undefined) });
  });

  it('lists the rooms with their state and the summary of the mix-ups', async () => {
    wrap();
    const rows = await screen.findAllByTestId('room-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Mapped');
    expect(rows[0]).toHaveTextContent('89%');
    expect(rows[1]).toHaveTextContent('Not mapped');
    const summary = screen.getByTestId('room-map-summary');
    expect(summary).toHaveTextContent('Overall accuracy 78%');
    expect(within(summary).getByTestId('room-map-confused')).toHaveTextContent('Den and Loft (6 samples)');
  });

  it('records a room with this phone: instructions, countdown, done, another pass', async () => {
    vi.mocked(roomMap.startMapSession).mockResolvedValue({ session_id: 's1', area_id: 'loft', tracker: `${UUID}-1-2`, ends_at: '' });
    vi.mocked(roomMap.getMapSession)
      .mockResolvedValueOnce({ status: 'recording', samples: 12, scanners_heard: 2, seconds_left: 41 })
      .mockResolvedValue({ status: 'done', samples: 60, scanners_heard: 3, seconds_left: 0 });
    wrap();
    expect(await screen.findByRole('option', { name: 'This phone' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('What you will carry')).toHaveValue('this-phone'));
    fireEvent.click(screen.getByRole('button', { name: 'Map Loft' }));
    expect(screen.getByText(/Walk slowly around the room, including corners/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('room-map-start'));
    await waitFor(() => expect(roomMap.startMapSession).toHaveBeenCalledWith('loft', `${UUID}-1-2`, 60));
    expect(await screen.findByTestId('room-map-done', {}, { timeout: 3000 })).toHaveTextContent('60 samples from 3 listeners');
    fireEvent.click(screen.getByRole('button', { name: /Record another pass/ }));
    expect(screen.getByTestId('room-map-start')).toBeInTheDocument();
  });

  it('says the phone is not heard on a 409, and notes when the screen cannot be held on', async () => {
    vi.mocked(roomMap.startMapSession).mockRejectedValueOnce(new AxiosError('x', 'ERR', undefined, undefined, {
      status: 409, statusText: '', headers: {}, config: { headers: new AxiosHeaders() }, data: { detail: 'Not heard in the last 30 s' },
    }));
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: 'Map Den' }));
    fireEvent.click(screen.getByTestId('room-map-start'));
    expect(await screen.findByTestId('room-map-error')).toHaveTextContent("Your phone isn't being heard. Check that Room detection is on in the Jarvis app.");

    vi.mocked(keepAwake.holdScreenOn).mockResolvedValue(null);
    vi.mocked(roomMap.startMapSession).mockResolvedValue({ session_id: 's2', area_id: 'den', tracker: 't', ends_at: '' });
    vi.mocked(roomMap.getMapSession).mockResolvedValue({ status: 'recording', samples: 1, scanners_heard: 1, seconds_left: 59 });
    fireEvent.click(screen.getByTestId('room-map-start'));
    expect(await screen.findByTestId('room-map-wake-note')).toBeInTheDocument();
    // The countdown starts at the full duration and follows the hub from its first poll.
    await waitFor(() => expect(screen.getByTestId('room-map-countdown')).toHaveTextContent('0:59'));
    fireEvent.click(screen.getByRole('button', { name: /Cancel/ }));
    await waitFor(() => expect(roomMap.cancelMapSession).toHaveBeenCalledWith('s2'));
  });

  it('offers other phones when this one is not a live beacon, and clears a room and the whole map after asking', async () => {
    platform.name = 'web';
    wrap();
    await waitFor(() => expect(screen.getByLabelText('What you will carry')).toHaveValue('sensor.alex_s_phone'));
    expect(screen.queryByRole('option', { name: 'This phone' })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Clear Den' }));
    await waitFor(() => expect(roomMap.clearMappedRoom).toHaveBeenCalledWith('den'));
    fireEvent.click(screen.getByRole('button', { name: /Clear all/ }));
    await waitFor(() => expect(roomMap.clearWholeMap).toHaveBeenCalled());
    expect(confirmDialog).toHaveBeenCalledTimes(2);
  });
});
