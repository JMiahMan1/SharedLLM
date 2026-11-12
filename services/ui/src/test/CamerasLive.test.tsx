import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import Cameras from '../pages/Cameras';
import { resetMediaTokenForTests } from '../lib/mediaToken';
import { resetModeMemory } from '../lib/cameraLive';

const LIVE_CAMERA = {
  entity_id: 'camera.porch_fluent', name: 'Porch Fluent', area: 'Front', platform: 'reolink', state: 'idle',
  recordings: false, live: ['snapshot', 'mjpeg'], stream: 'snapshots', snapshot_interval: 0.5,
  realtime: { modes: ['webrtc', 'mse', 'mp4'], qualities: ['sub', 'main'], default_quality: 'sub', on_demand: true },
};
const PLAIN_CAMERA = {
  entity_id: 'camera.printer', name: 'Printer', area: 'Office', platform: 'mjpeg', state: 'idle',
  recordings: false, live: ['snapshot', 'mjpeg'], stream: 'camera',
};

const SETTINGS = {
  configured: true, entry_id: 'e1', enabled: true, state: 'loaded', reason: null, url: 'http://127.0.0.1:1984',
  username: '', has_password: false,
  options: { webrtc_host: '', webrtc_port: 8555, default_quality: 'sub' },
  server: { version: '1.9.15', error: null },
  cameras: [
    { entity_id: 'camera.porch_fluent', name: 'Porch', platform: 'reolink', settings: { quality: 'main' },
      qualities: ['sub', 'main'], registered: ['main', 'sub'], battery: true, on_demand: true, note: null },
    { entity_id: 'camera.printer', name: 'Printer', platform: 'mjpeg', settings: {}, qualities: [], registered: [],
      note: 'an MJPEG camera' },
  ],
};

let saved: unknown[] = [];

beforeEach(() => {
  resetMediaTokenForTests();
  resetModeMemory();
  saved = [];
  server.use(
    http.get('/api/cameras', () => HttpResponse.json({ cameras: [LIVE_CAMERA, PLAIN_CAMERA] })),
    http.get('/api/admin/cameras/live-video', () => HttpResponse.json(SETTINGS)),
    http.put('/api/admin/cameras/live-video', async ({ request }) => {
      saved.push(await request.json());
      return HttpResponse.json(SETTINGS);
    }),
  );
});

afterEach(() => {
  document.body.style.overflow = '';
});

describe('Cameras page real-time video', () => {
  it('plays a camera go2rtc offers in a video player, through the gateway with the media token', async () => {
    // jsdom has neither WebRTC nor MSE, so the player starts at the plain MP4 stream.
    renderWithProviders(<Cameras />, { route: '/cameras?camera=camera.porch_fluent' });
    const dialog = await screen.findByRole('dialog', { name: 'Porch' });
    const video = await waitFor(() => {
      const v = dialog.querySelector('video');
      expect(v?.getAttribute('src')).toBeTruthy();
      return v!;
    });
    const src = video.getAttribute('src')!;
    expect(src).toContain('/api/media/cameras/camera.porch_fluent/live/stream.mp4?');
    expect(src).toContain('quality=sub');
    expect(src).toContain('mt=mt-1');
    expect(src).not.toContain('test-token');
    expect(within(dialog).queryByRole('img', { name: 'Porch live' })).not.toBeInTheDocument();
    expect(within(dialog).getByText('Real-time video.', { exact: false })).toBeInTheDocument();
  });

  it('switches quality from the player', async () => {
    renderWithProviders(<Cameras />, { route: '/cameras?camera=camera.porch_fluent' });
    const dialog = await screen.findByRole('dialog', { name: 'Porch' });
    await userEvent.click(await within(dialog).findByRole('button', { name: 'HD' }));
    await waitFor(() => expect(dialog.querySelector('video')!.getAttribute('src')).toContain('quality=main'));
    expect(within(dialog).getByRole('button', { name: 'HD' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('falls back to the picture stream when real-time video fails', async () => {
    renderWithProviders(<Cameras />, { route: '/cameras?camera=camera.porch_fluent' });
    const dialog = await screen.findByRole('dialog', { name: 'Porch' });
    const video = await waitFor(() => {
      const v = dialog.querySelector('video');
      expect(v?.getAttribute('src')).toBeTruthy();
      return v!;
    });
    video.dispatchEvent(new Event('error'));
    const img = await within(dialog).findByRole('img', { name: 'Porch live' });
    expect(img.getAttribute('src')).toContain('/api/media/cameras/camera.porch_fluent/stream?');
    expect(dialog.querySelector('video')).toBeNull();
  });

  it('keeps the picture stream for a camera go2rtc does not play', async () => {
    renderWithProviders(<Cameras />, { route: '/cameras?camera=camera.printer' });
    const dialog = await screen.findByRole('dialog', { name: 'Printer' });
    const img = await within(dialog).findByRole('img', { name: 'Printer live' });
    expect(img.getAttribute('src')).toContain('/stream?');
    expect(dialog.querySelector('video')).toBeNull();
  });

  it('lets an admin change the live video settings', async () => {
    renderWithProviders(<Cameras />, { route: '/cameras' });
    await userEvent.click(await screen.findByText('Live video settings'));
    expect(await screen.findByText(/Running \(go2rtc 1\.9\.15\)/)).toBeInTheDocument();
    // A battery camera always streams on demand: the switch is there but cannot be turned off.
    const cams = screen.getByRole('list', { name: 'Cameras' });
    expect(within(cams).getByLabelText(/Only while watched \(battery camera\)/)).toBeDisabled();
    expect(within(cams).getByText('an MJPEG camera')).toBeInTheDocument();
    const port = screen.getByLabelText('WebRTC port');
    await userEvent.clear(port);
    await userEvent.type(port, '18555');
    await userEvent.selectOptions(within(cams).getByLabelText('Porch quality'), '');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0]).toMatchObject({ webrtc_port: 18555, enabled: true, cameras: { 'camera.porch_fluent': {} } });
  });
});
