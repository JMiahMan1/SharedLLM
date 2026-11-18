import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const status = { homehub: { enabled: false }, home_assistant: { enabled: false } };
const getIntegrationsStatus = vi.fn(async () => status);
vi.mock('../services/api', () => ({
  api: {
    getIntegrationsStatus: () => getIntegrationsStatus(),
    updateWidgetSettings: vi.fn(async () => ({})),
    getWidgetSettings: vi.fn(async () => ({ widgets: {} })),
  },
}));

let admin = true;
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'sample', is_admin: admin }, role: admin ? 'admin' : 'user' }),
}));

import {
  EVERY_HUB, NAV_PAGES, hubRequirementMet, navPagesFor, navSectionsFor, type HubIntegrations,
} from '../components/layout/navItems';
import HubRequired from '../components/layout/HubRequired';
import { CONNECT_HUB_TITLE, homehubStatusLine } from '../lib/homehub';
import { defaultWidgetDefs, isWidgetAvailable, isWidgetVisible } from '../stores/widgetStore';

const NONE: HubIntegrations = { homehub: false, homeAssistant: false };
const HA_ONLY: HubIntegrations = { homehub: false, homeAssistant: true };
const HOMEHUB_ONLY: HubIntegrations = { homehub: true, homeAssistant: false };
const paths = (hubs: HubIntegrations, isAdmin = true) => navPagesFor(isAdmin, hubs).map((p) => p.path);

const HOMEHUB_PAGES = ['/rooms', '/climate', '/cameras', '/energy', '/schedules', '/automations'];
const ANY_HUB_PAGES = ['/remote', '/history', '/lists'];

describe('nav pages and the home integrations', () => {
  it('lists every page when both integrations are on (the default)', () => {
    expect(navPagesFor(true)).toEqual(navPagesFor(true, EVERY_HUB));
    for (const path of [...HOMEHUB_PAGES, ...ANY_HUB_PAGES]) expect(paths(EVERY_HUB)).toContain(path);
  });

  it('hides every hub page with no hub, for admins and members alike', () => {
    for (const isAdmin of [true, false]) {
      const shown = paths(NONE, isAdmin);
      for (const path of [...HOMEHUB_PAGES, ...ANY_HUB_PAGES]) expect(shown).not.toContain(path);
    }
  });

  it('keeps the pages that work without a hub, Devices and Media included', () => {
    const shown = paths(NONE);
    for (const path of ['/', '/wander', '/family', '/media', '/devices', '/notes', '/calendar', '/settings', '/admin']) {
      expect(shown).toContain(path);
    }
  });

  it('shows the any-hub pages with Home Assistant alone, but not the HomeHub ones', () => {
    const shown = paths(HA_ONLY);
    for (const path of ANY_HUB_PAGES) expect(shown).toContain(path);
    for (const path of HOMEHUB_PAGES) expect(shown).not.toContain(path);
  });

  it('shows every hub page with HomeHub alone', () => {
    const shown = paths(HOMEHUB_ONLY);
    for (const path of [...HOMEHUB_PAGES, ...ANY_HUB_PAGES]) expect(shown).toContain(path);
  });

  it('leaves out the Home section entirely when no hub is on', () => {
    const keys = navSectionsFor(true, navPagesFor(true, NONE)).map((s) => s.key);
    expect(keys).not.toContain('home');
  });

  it('marks exactly the hub pages as needing one', () => {
    const marked = NAV_PAGES.filter((p) => p.requires).map((p) => p.path).sort();
    expect(marked).toEqual([...HOMEHUB_PAGES, ...ANY_HUB_PAGES].sort());
  });

  it('reads a requirement against the integrations', () => {
    expect(hubRequirementMet(undefined, NONE)).toBe(true);
    expect(hubRequirementMet('hub', NONE)).toBe(false);
    expect(hubRequirementMet('hub', HA_ONLY)).toBe(true);
    expect(hubRequirementMet('homehub', HA_ONLY)).toBe(false);
    expect(hubRequirementMet('homehub', HOMEHUB_ONLY)).toBe(true);
  });
});

describe('dashboard widgets and the home hub', () => {
  const HUB_WIDGETS = ['energy_insights', 'device_control', 'climate'];
  const def = (key: string) => defaultWidgetDefs.find((d) => d.key === key)!;
  const ctx = (hub: boolean) => ({
    userWidgets: {}, capabilities: { has_home_hub: hub }, quickAssistantEnabled: false, isAdmin: true,
  });

  it('mounts the hub widgets only with a hub connected', () => {
    for (const key of HUB_WIDGETS) {
      expect(isWidgetVisible(def(key), ctx(false))).toBe(false);
      expect(isWidgetVisible(def(key), ctx(true))).toBe(true);
      expect(isWidgetAvailable(def(key), { capabilities: {}, isAdmin: true })).toBe(false);
    }
  });

  it('keeps widgets that work without a hub', () => {
    for (const key of ['active_media', 'presence', 'ambient_timer', 'upcoming_events', 'weather', 'bible_daily']) {
      expect(isWidgetVisible(def(key), ctx(false))).toBe(true);
    }
  });

  it('gates no widget other than the hub ones', () => {
    const gated = defaultWidgetDefs.filter((d) => d.mountConditions).map((d) => d.key).sort();
    expect(gated).toEqual([...HUB_WIDGETS].sort());
  });
});

describe('HubRequired', () => {
  const renderGate = (requires: 'hub' | 'homehub') => render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <HubRequired requires={requires} page="Rooms"><p>rooms page</p></HubRequired>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  beforeEach(() => {
    admin = true;
    status.homehub.enabled = false;
    status.home_assistant.enabled = false;
  });

  it('says how to connect a hub instead of showing the page when none is on', async () => {
    renderGate('homehub');
    expect(await screen.findByText(CONNECT_HUB_TITLE)).toBeInTheDocument();
    expect(screen.queryByText('rooms page')).toBeNull();
    expect(screen.getByRole('link', { name: 'Open Accounts & Integrations' })).toHaveAttribute('href', '/identity');
  });

  it('tells a member to ask an admin, with no link to a page they cannot use', async () => {
    admin = false;
    renderGate('hub');
    expect(await screen.findByText(/Ask an admin/)).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('shows the page once its integration is on', async () => {
    status.homehub.enabled = true;
    renderGate('homehub');
    expect(await screen.findByText('rooms page')).toBeInTheDocument();
  });

  it('needs HomeHub itself for a HomeHub page, Home Assistant is not enough', async () => {
    status.home_assistant.enabled = true;
    renderGate('homehub');
    expect(await screen.findByText(CONNECT_HUB_TITLE)).toBeInTheDocument();
  });

  it('takes Home Assistant for a page any hub can serve', async () => {
    status.home_assistant.enabled = true;
    renderGate('hub');
    expect(await screen.findByText('rooms page')).toBeInTheDocument();
  });
});

describe('the HomeHub card status line', () => {
  it('says what is running and what waits for a restart', () => {
    expect(homehubStatusLine(true, 'http://hub.example.test:8123')).toBe('Connected: hub.example.test:8123');
    expect(homehubStatusLine(true, null)).toBe('Connected');
    expect(homehubStatusLine(false, 'http://hub.example.test')).toBe('Saved; applies after a restart');
    expect(homehubStatusLine(false, null)).toBe('Off: no address set');
  });
});
