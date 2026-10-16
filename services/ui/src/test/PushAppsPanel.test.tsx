import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './setup';
import { renderWithProviders } from './render';
import PushAppsPanel from '../components/settings/PushAppsPanel';

const plugin = vi.hoisted(() => ({
  appLabels: vi.fn(),
  removeApp: vi.fn(),
}));

vi.mock('../plugins/chatNotify', () => ({ default: plugin }));

const TALK = { app: 'com.nextcloud.talk2', token: 'tok-talk', topic: 'upTalk' };
const DAVX = { app: 'at.bitfire.davdroid', token: 'tok-davx', topic: 'upDavx' };

describe('PushAppsPanel', () => {
  beforeEach(() => {
    plugin.appLabels.mockReset();
    plugin.removeApp.mockReset();
    plugin.appLabels.mockResolvedValue({ labels: { 'com.nextcloud.talk2': 'Talk' } });
  });

  it('lists apps by the names people know them by', async () => {
    server.use(http.get('/api/push/up', () => HttpResponse.json([TALK, DAVX])));
    renderWithProviders(<PushAppsPanel />);

    expect(await screen.findByText('Talk')).toBeInTheDocument();
    expect(screen.getByText('com.nextcloud.talk2')).toBeInTheDocument();
    // No label known: the package name stands in.
    expect(screen.getByText('at.bitfire.davdroid')).toBeInTheDocument();
    expect(plugin.appLabels).toHaveBeenCalledWith({ packages: ['at.bitfire.davdroid', 'com.nextcloud.talk2'] });
  });

  it('says when no app uses Jarvis for push', async () => {
    renderWithProviders(<PushAppsPanel />);
    expect(await screen.findByText(/No apps use Jarvis for push yet/i)).toBeInTheDocument();
  });

  it('removes an app through the phone so the app is told', async () => {
    let gatewayCalled = false;
    const apps = [TALK];
    server.use(
      http.get('/api/push/up', () => HttpResponse.json(apps)),
      http.post('/api/push/up/unregister', () => {
        gatewayCalled = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    plugin.removeApp.mockImplementation(async () => {
      apps.length = 0;
      return { removed: 1 };
    });
    renderWithProviders(<PushAppsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: 'Remove Talk' }));

    await waitFor(() => expect(plugin.removeApp).toHaveBeenCalledWith({ app: 'com.nextcloud.talk2', token: 'tok-talk' }));
    expect(await screen.findByText(/No apps use Jarvis for push yet/i)).toBeInTheDocument();
    expect(gatewayCalled).toBe(false);
  });

  it('removes a registration from another phone at the gateway', async () => {
    let body: unknown = null;
    server.use(
      http.get('/api/push/up', () => HttpResponse.json({ registrations: [DAVX] })),
      http.post('/api/push/up/unregister', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true });
      }),
    );
    plugin.removeApp.mockResolvedValue({ removed: 0 });
    renderWithProviders(<PushAppsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: 'Remove at.bitfire.davdroid' }));
    await waitFor(() => expect(body).toEqual({ app: 'at.bitfire.davdroid', token: 'tok-davx' }));
  });

  it('still removes at the gateway on an app build without removeApp', async () => {
    let called = false;
    server.use(
      http.get('/api/push/up', () => HttpResponse.json([TALK])),
      http.post('/api/push/up/unregister', () => {
        called = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    plugin.appLabels.mockRejectedValue(new Error('not implemented'));
    plugin.removeApp.mockRejectedValue(new Error('not implemented'));
    renderWithProviders(<PushAppsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: 'Remove com.nextcloud.talk2' }));
    await waitFor(() => expect(called).toBe(true));
  });

  it('gives each remove button a 44px target', async () => {
    server.use(http.get('/api/push/up', () => HttpResponse.json([TALK])));
    renderWithProviders(<PushAppsPanel />);
    const button = await screen.findByRole('button', { name: 'Remove Talk' });
    expect(button.className).toContain('min-h-[44px]');
    expect(button.className).toContain('min-w-[44px]');
  });
});
