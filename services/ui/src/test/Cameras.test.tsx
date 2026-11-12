import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import Cameras from '../pages/Cameras';
import { resetMediaTokenForTests } from '../lib/mediaToken';

const CAMERAS = [
  {
    entity_id: 'camera.garage_door_camera', name: 'Garage Door Camera', area: 'Garage', platform: 'esphome',
    state: 'idle', recordings: false, live: ['snapshot', 'mjpeg'], stream: 'snapshots', snapshot_interval: 1,
  },
  {
    entity_id: 'camera.backyard_camera_1_fluent', name: 'Backyard Camera 1 Fluent', area: 'Backyard',
    platform: 'reolink', state: 'idle', recordings: true, live: ['snapshot', 'mjpeg'], stream: 'snapshots',
    snapshot_interval: 0.5, battery: true, sleeping: true, model: 'Argus Eco',
  },
  {
    entity_id: 'camera.shed', name: 'Shed', area: 'Yard', platform: 'mjpeg', state: 'unavailable',
    recordings: false, live: ['snapshot', 'mjpeg'], stream: 'camera',
  },
];

const FILE_A = 'Mp4Record/2026-11-10/RecM07_DST20261110_201000_201045.mp4';
const FILE_B = 'Mp4Record/2026-11-10/RecM07_DST20261110_081500_081530.mp4';

let recordingQueries: URLSearchParams[] = [];

beforeEach(() => {
  resetMediaTokenForTests();
  recordingQueries = [];
  server.use(
    http.get('/api/cameras', () => HttpResponse.json({ cameras: CAMERAS })),
    http.get('/api/cameras/:entityId/recordings', ({ request }) => {
      recordingQueries.push(new URL(request.url).searchParams);
      return HttpResponse.json({
        entity_id: 'camera.backyard_camera_1_fluent',
        start: '2026-11-10T00:00:00Z',
        end: '2026-11-10T23:59:59Z',
        recordings: [
          { file: FILE_A, start: '2026-11-10T20:10:00-07:00', end: '2026-11-10T20:10:45-07:00', duration: 45, triggers: ['vehicle'] },
          { file: FILE_B, start: '2026-11-10T08:15:00-07:00', end: '2026-11-10T08:15:30-07:00', duration: 30, triggers: ['person', 'motion'] },
        ],
      });
    }),
  );
});

afterEach(() => {
  document.body.style.overflow = '';
});

describe('Cameras page', () => {
  it('shows a tile per camera with a signed picture, and says which are offline', async () => {
    renderWithProviders(<Cameras />, { route: '/cameras' });
    const garage = await screen.findByRole('button', { name: 'Open Garage Door Camera' });
    // The picture waits for the media token, then carries it (never the API key).
    await waitFor(() => expect(garage.querySelector('img')).not.toBeNull());
    const src = garage.querySelector('img')!.getAttribute('src')!;
    expect(src).toContain('/api/media/cameras/camera.garage_door_camera/snapshot?');
    expect(src).toContain('mt=mt-1');
    expect(src).toContain('user=alice');
    expect(src).not.toContain('test-token');
    // The stream's name is not part of a camera's name.
    expect(screen.getByRole('button', { name: 'Open Backyard Camera 1' })).toBeInTheDocument();
    expect(screen.getByText('Asleep')).toBeInTheDocument();
    const shed = screen.getByRole('button', { name: 'Open Shed' });
    expect(within(shed).getByText('Offline')).toBeInTheDocument();
    expect(shed.querySelector('img')).toBeNull();
  });

  it('opens a full live view on tap', async () => {
    renderWithProviders(<Cameras />, { route: '/cameras' });
    await userEvent.click(await screen.findByRole('button', { name: 'Open Garage Door Camera' }));
    const dialog = await screen.findByRole('dialog', { name: 'Garage Door Camera' });
    const live = await within(dialog).findByRole('img', { name: 'Garage Door Camera live' });
    expect(live.getAttribute('src')).toContain('/api/media/cameras/camera.garage_door_camera/stream?');
    // No recordings, so no tabs.
    expect(within(dialog).queryByRole('tab', { name: 'Recordings' })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('says a live view that fails is not answering and offers to try again', async () => {
    renderWithProviders(<Cameras />, { route: '/cameras?camera=camera.garage_door_camera' });
    const dialog = await screen.findByRole('dialog', { name: 'Garage Door Camera' });
    const live = await within(dialog).findByRole('img', { name: 'Garage Door Camera live' });
    live.dispatchEvent(new Event('error'));
    expect(await within(dialog).findByText('Garage Door Camera is not answering')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Try again' }));
    const again = await within(dialog).findByRole('img', { name: 'Garage Door Camera live' });
    expect(again.getAttribute('src')).toContain('n=1');
  });

  it('lists a day of recordings with what set them off, and plays one', async () => {
    renderWithProviders(<Cameras />, { route: '/cameras?camera=camera.backyard_camera_1_fluent' });
    const dialog = await screen.findByRole('dialog', { name: 'Backyard Camera 1' });
    await userEvent.click(within(dialog).getByRole('tab', { name: 'Recordings' }));
    const list = await within(dialog).findByRole('list', { name: 'Recordings' });
    const rows = within(list).getAllByRole('button');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('Vehicle')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Person')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Motion')).toBeInTheDocument();
    expect(within(rows[1]).getByText('0:30')).toBeInTheDocument();
    // The query covers the chosen local day.
    const q = recordingQueries[0];
    expect(new Date(q.get('start')!).getHours()).toBe(0);
    await userEvent.click(rows[1]);
    const video = dialog.querySelector('video');
    expect(video).not.toBeNull();
    const src = video!.getAttribute('src')!;
    expect(src).toContain('/api/media/cameras/camera.backyard_camera_1_fluent/recordings/play?');
    expect(new URL(src).searchParams.get('file')).toBe(FILE_B);
    expect(src).toContain('mt=mt-1');
  });

  it('moves to the previous day and will not go past today', async () => {
    renderWithProviders(<Cameras />, { route: '/cameras?camera=camera.backyard_camera_1_fluent&view=recordings' });
    const dialog = await screen.findByRole('dialog', { name: 'Backyard Camera 1' });
    await within(dialog).findByRole('list', { name: 'Recordings' });
    expect(within(dialog).getByRole('button', { name: 'Next day' })).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Previous day' }));
    await waitFor(() => expect(recordingQueries).toHaveLength(2));
    const first = new Date(recordingQueries[0].get('start')!);
    const second = new Date(recordingQueries[1].get('start')!);
    expect(first.getTime() - second.getTime()).toBeGreaterThanOrEqual(23 * 3600_000);
    expect(within(dialog).getByRole('button', { name: 'Next day' })).toBeEnabled();
  });

  it('shows the hub being down as an error with a way to try again', async () => {
    server.use(http.get('/api/cameras', () => HttpResponse.json({ detail: 'The camera hub did not answer' }, { status: 502 })));
    renderWithProviders(<Cameras />, { route: '/cameras' });
    expect(await screen.findByText('The camera hub did not answer')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('says when there are no cameras to show', async () => {
    server.use(http.get('/api/cameras', () => HttpResponse.json({ cameras: [] })));
    renderWithProviders(<Cameras />, { route: '/cameras' });
    expect(await screen.findByText('No cameras yet')).toBeInTheDocument();
  });
});
