import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import DisplayKiosk from '../pages/DisplayKiosk';
import type { WeatherResponse } from '../lib/weather';
import type { CallInfo } from '../context/CallContext';
import type { ActiveCall } from '../types/api';
import { server } from './setup';

const weatherState = vi.hoisted(() => ({
  data: null as WeatherResponse | null,
  isPending: false,
  isError: false,
  error: null as Error | null,
}));

vi.mock('../hooks/useWeather', () => ({ useWeather: () => weatherState }));

const callState = vi.hoisted(() => ({
  call: null as CallInfo | null,
  activeCalls: [] as ActiveCall[],
  muted: false,
  cameraOn: false,
  startCall: vi.fn<(room: string, name: string, options?: unknown) => Promise<void>>(async () => {}),
  endCall: vi.fn(),
  toggleMute: vi.fn(),
  toggleCamera: vi.fn(async () => {}),
}));

vi.mock('../context/CallContext', () => ({ useCall: () => callState }));

const forecast: WeatherResponse = {
  source: 'test',
  location: { name: 'Home', latitude: 0, longitude: 0 },
  timezone: 'UTC',
  utc_offset_seconds: 0,
  units: { system: 'metric', temp: '°C', wind: 'km/h', precip: 'mm' },
  current: {
    ts: 0, temp: 21.4, feels_like: 20, humidity: 50, precip: 0, code: 2,
    label: 'Partly cloudy', icon: 'partly_cloudy', wind: 5, gust: 8, is_day: true,
  },
  hourly: [],
  daily: [],
  fetched_at: 1767225600,
};

const ENTITIES = [
  { entity_id: 'sensor.crown_drop_in', friendly_name: 'Crown Drop In', state: 'idle', domain: 'sensor' },
  { entity_id: 'assist_satellite.crown', friendly_name: 'Echo Show Crown', state: 'idle', domain: 'assist_satellite' },
  { entity_id: 'sensor.crown_last_heard', friendly_name: 'Crown Last Heard', state: 'turn on the kitchen lights', domain: 'sensor' },
  { entity_id: 'sensor.crown_last_reply', friendly_name: 'Crown Last Reply', state: 'Done', domain: 'sensor' },
  { entity_id: 'media_player.kitchen', friendly_name: 'Kitchen Music', state: 'playing', domain: 'media_player' },
  { entity_id: 'light.kitchen', friendly_name: 'Kitchen Light', state: 'off', domain: 'light' },
];

const ENTITIES_WITH_VOLUME = ENTITIES.map((entity) => entity.entity_id === 'media_player.kitchen'
  ? { ...entity, attributes: { volume_level: 0.4 } }
  : entity);

const TIMERS = [{
  id: 't1', type: 'timer', title: 'Kitchen Timer', expires_at: '2026-05-06T00:00:00Z', active: true,
}];

const CAMERAS = [{
  entity_id: 'camera.front_door', name: 'Front Door', platform: 'generic', state: 'idle', recordings: false, live: true,
}];

function renderKiosk() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const result = render(
    <QueryClientProvider client={client}>
      <DisplayKiosk />
    </QueryClientProvider>,
  );
  return { ...result, client, invalidate };
}

beforeEach(() => {
  weatherState.data = forecast;
  weatherState.isPending = false;
  weatherState.isError = false;
  weatherState.error = null;
  callState.call = null;
  callState.activeCalls = [];
  callState.muted = false;
  callState.cameraOn = false;
  vi.clearAllMocks();
  callState.startCall.mockReset().mockResolvedValue(undefined);
  callState.toggleCamera.mockReset().mockResolvedValue(undefined);

  server.use(
    http.get('/api/entities', () => HttpResponse.json({ entities: ENTITIES })),
    http.get('/api/communication/timers', () => HttpResponse.json(TIMERS)),
    http.get('/api/cameras', () => HttpResponse.json({ cameras: CAMERAS })),
  );
});

describe('DisplayKiosk', () => {
  it('renders the live clock/date and the current forecast from the API', async () => {
    renderKiosk();

    expect(await screen.findByTestId('kiosk-clock')).not.toBeEmptyDOMElement();
    expect(screen.getByTestId('kiosk-date')).not.toBeEmptyDOMElement();
    const weather = await screen.findByTestId('kiosk-weather');
    expect(weather).toHaveTextContent('Partly cloudy');
    expect(weather).toHaveTextContent('Home');
    expect(weather).toHaveTextContent('21°C');
  });

  it('shows the live transcript and reply overlay and dismisses on tap', async () => {
    const user = userEvent.setup();
    renderKiosk();

    const overlay = await screen.findByTestId('voice-overlay');
    expect(overlay).toHaveTextContent('turn on the kitchen lights');
    expect(overlay).toHaveTextContent('Jarvis: Done');
    await user.click(overlay);
    expect(screen.queryByTestId('voice-overlay')).not.toBeInTheDocument();
  });

  it('lists Echo rooms for Drop-In and starts an audio call', async () => {
    const user = userEvent.setup();
    callState.activeCalls = [{
      token: 'family-room', name: 'Family', started_at: 1767225600, participants: [],
    }];
    renderKiosk();

    expect(await screen.findByTestId('dropin-bar')).toHaveTextContent('Family');
    await user.click(screen.getByRole('button', { name: 'Drop In rooms' }));
    const rooms = await screen.findByTestId('dropin-rooms');
    expect(rooms).toHaveTextContent('Echo Show Crown');
    await user.click(within(rooms).getByRole('button', { name: 'Drop In on Echo Show Crown' }));
    expect(callState.startCall).toHaveBeenCalledWith('crown', 'Echo Show Crown', { video: false });
  });

  it('toggles the smart camera/doorbell popup', async () => {
    const user = userEvent.setup();
    renderKiosk();

    await user.click(screen.getByRole('button', { name: 'Cameras' }));
    const popup = await screen.findByTestId('camera-popup');
    expect(popup).toHaveTextContent('Front Door');
    await user.click(within(popup).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByTestId('camera-popup')).not.toBeInTheDocument();
  });

  it('views hub cameras without probing (and switching on) the Show webcam', async () => {
    const getUserMedia = vi.fn();
    Object.defineProperty(window.navigator, 'mediaDevices', { writable: true, value: { getUserMedia } });
    const user = userEvent.setup();
    renderKiosk();

    await user.click(screen.getByRole('button', { name: 'Cameras' }));
    expect(await screen.findByTestId('camera-popup')).toHaveTextContent('Front Door');
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(screen.queryByText(/privacy shutter/i)).not.toBeInTheDocument();
  });

  it('shows the camera/privacy-shutter alert on the Drop-In bar when Answer Video fails', async () => {
    callState.activeCalls = [{
      token: 'family-room', name: 'Family', started_at: 1767225600, participants: [],
    }];
    callState.startCall.mockRejectedValueOnce(new Error('NotAllowedError: camera blocked'));
    const user = userEvent.setup();
    renderKiosk();

    const bar = await screen.findByTestId('dropin-bar');
    await user.click(within(bar).getByRole('button', { name: 'Answer (Video)' }));
    const alert = await within(bar).findByRole('alert');
    expect(alert).toHaveTextContent(/Camera unavailable/i);
    expect(alert).toHaveTextContent(/privacy shutter/i);
    expect(alert).toHaveTextContent(/camera blocked/i);
    expect(callState.startCall).toHaveBeenCalledWith('family-room', 'Family', { video: true, private: undefined });
  });

  it('shows the camera alert on the Drop-In bar when toggling the camera fails', async () => {
    callState.toggleCamera.mockRejectedValueOnce(new Error('camera busy'));
    const user = userEvent.setup();
    renderKiosk();

    const bar = await screen.findByTestId('dropin-bar');
    await user.click(within(bar).getByRole('button', { name: 'Camera' }));
    expect(await within(bar).findByRole('alert')).toHaveTextContent(/Camera unavailable.*camera busy/i);
  });

  it('names the camera gap before starting video or enabling the camera', async () => {
    callState.activeCalls = [{
      token: 'family-room', name: 'Family', started_at: 1767225600, participants: [],
    }];
    Object.defineProperty(window.navigator, 'mediaDevices', { writable: true, value: undefined });
    const user = userEvent.setup();
    renderKiosk();

    const bar = await screen.findByTestId('dropin-bar');
    await user.click(within(bar).getByRole('button', { name: 'Answer (Video)' }));
    expect(await within(bar).findByRole('alert')).toHaveTextContent(/Camera unavailable.*privacy shutter/i);
    expect(callState.startCall).not.toHaveBeenCalled();

    await user.click(within(bar).getByRole('button', { name: 'Camera' }));
    expect(callState.toggleCamera).not.toHaveBeenCalled();
  });

  it('initialises the volume slider from the volume reported by Home Hub', async () => {
    server.use(http.get('/api/entities', () => HttpResponse.json({ entities: ENTITIES_WITH_VOLUME })));
    renderKiosk();

    const slider = await screen.findByRole('slider', { name: 'Volume' }) as HTMLInputElement;
    expect(slider.value).toBe('40');
    expect(slider).toBeEnabled();
  });

  it('disables the volume slider instead of pretending a volume was reported', async () => {
    renderKiosk();

    const slider = await screen.findByRole('slider', { name: 'Volume' }) as HTMLInputElement;
    expect(slider).toBeDisabled();
    expect(slider.value).not.toBe('50');
    expect(screen.getByText(/Volume not reported/i)).toBeInTheDocument();
  });

  it('refreshes states and timers after timer, media and room actions', async () => {
    const user = userEvent.setup();
    const { invalidate } = renderKiosk();

    await user.click(await screen.findByRole('button', { name: 'Stop Kitchen Timer' }));
    await user.click(screen.getByRole('button', { name: 'Play music' }));
    await user.click(screen.getByRole('button', { name: 'Toggle Kitchen Light' }));

    await waitFor(() => {
      const refreshes = (key: string[]) => invalidate.mock.calls.filter(
        ([filters]) => JSON.stringify(filters?.queryKey) === JSON.stringify(key),
      );
      expect(refreshes(['display-kiosk', 'states'])).toHaveLength(3);
      expect(refreshes(['display-kiosk', 'timers'])).toHaveLength(3);
    });
  });

  it('gives every interactive control a 44px coarse-pointer touch target', async () => {
    const user = userEvent.setup();
    renderKiosk();

    await user.click(screen.getByRole('button', { name: 'Drop In rooms' }));
    await user.click(screen.getByRole('button', { name: 'Cameras' }));
    await screen.findByTestId('camera-popup');

    const interactive = Array.from(document.querySelectorAll('button, select, a, input'));
    expect(interactive.length).toBeGreaterThan(10);
    for (const element of interactive) {
      expect(element.className, element.outerHTML).toContain('min-h-11');
      expect(element.className, element.outerHTML).toContain('pointer-coarse:min-h-11');
    }
  });
});
