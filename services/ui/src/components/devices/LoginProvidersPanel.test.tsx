import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import LoginProvidersPanel from './LoginProvidersPanel';
import PersonsPanel from './PersonsPanel';
import * as setup from '../../lib/homeSetup';
import { syncSummary, type HubUser, type LoginProvider, type LoginProviders } from '../../lib/homeSetup';

vi.mock('../../lib/homeSetup', async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown>;
  return {
    ...real,
    getLoginProviders: vi.fn(), setLoginProviders: vi.fn(), syncLoginProvider: vi.fn(),
    getHubUsers: vi.fn(), createHubUser: vi.fn(), updateHubUser: vi.fn(),
    getPersons: vi.fn(), createPerson: vi.fn(), updatePerson: vi.fn(), deletePerson: vi.fn(),
  };
});
vi.mock('../../services/api', async (importOriginal) => {
  const real = (await importOriginal()) as { api: Record<string, unknown> };
  return { ...real, api: { ...real.api, getFamily: vi.fn(async () => []) } };
});
vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { default: toast };
});

const m = vi.mocked(setup);

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const local: LoginProvider = { type: 'local', label: 'This hub', external: false, configured: true, active: true,
  primary: false, entry_id: null, state: 'loaded', reason: null, users: 1, inactive_users: 0, last_sync: null };
const jarvis: LoginProvider = { type: 'jarvis_identity', label: 'Jarvis', external: true, configured: true, active: true,
  primary: true, entry_id: 'e1', state: 'loaded', reason: null, users: 3, inactive_users: 1,
  last_sync: { ok: true, at: new Date().toISOString(), provider_users: 3, users_added: 1, users_matched: 2, persons_linked: 2 } };

function status(over: Partial<LoginProviders> = {}): LoginProviders {
  return { providers: [local, jarvis], primary: 'jarvis_identity', local_logins: true, local_logins_setting: true,
    external_admins: ['robin'], standalone_users: ['Visitor'], ...over };
}

const users: HubUser[] = [
  { id: 'u1', name: 'Robin', source: 'jarvis_identity', username: 'robin', is_admin: true, is_owner: false, is_active: true,
    hub_login: false, editable: false, provider: { type: 'jarvis_identity', username: 'robin', roles: ['father'], avatar: null, removed: false } },
  { id: 'u2', name: 'Visitor', source: 'local', username: 'visitor', is_admin: false, is_owner: false, is_active: true,
    hub_login: true, editable: true, provider: null },
  { id: 'u3', name: 'Music', source: 'service', username: null, is_admin: false, is_owner: false, is_active: true,
    hub_login: false, editable: false, provider: null },
];

beforeEach(() => {
  vi.clearAllMocks();
  m.getLoginProviders.mockResolvedValue(status());
  m.getHubUsers.mockResolvedValue(users);
});

describe('syncSummary', () => {
  it('says what a sync did, or why it failed', () => {
    expect(syncSummary(null)).toBe('Not synced yet');
    expect(syncSummary({ ok: false, error: 'cannot be reached' })).toBe('Last sync failed: cannot be reached');
    expect(syncSummary({ ok: true, provider_users: 8, users_added: 1, persons_linked: 1 }))
      .toBe('8 users · 1 added · 1 person linked');
  });
});

describe('LoginProvidersPanel', () => {
  it('shows each provider, the primary, the last sync and the hub-only users', async () => {
    wrap(<LoginProvidersPanel />);
    const rows = await screen.findAllByTestId('login-provider');
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).getByText('Primary')).toBeTruthy();
    expect(within(rows[1]).getByTestId('login-provider-sync').textContent).toContain('3 users · 1 added · 2 linked');
    expect(screen.getByTestId('standalone-users').textContent).toContain('Visitor');
    expect((screen.getByLabelText('Primary login') as HTMLSelectElement).value).toBe('jarvis_identity');
    // Service logins are not people; Jarvis users are changed in Jarvis, hub users here.
    const people = await screen.findAllByTestId('hub-user');
    expect(people.map((p) => p.querySelector('span')?.textContent)).toEqual(['Robin', 'Visitor']);
    expect(within(people[0]).getByText('From Jarvis')).toBeTruthy();
    expect(within(people[0]).queryByRole('button')).toBeNull();
    expect(within(people[1]).getByRole('button', { name: 'Make admin' })).toBeTruthy();
  });

  it('syncs now, changes the primary and turns hub passwords off', async () => {
    m.syncLoginProvider.mockResolvedValue({ ok: true, provider_users: 3 });
    m.setLoginProviders.mockResolvedValueOnce(status({ primary: 'local' }))
      .mockResolvedValueOnce(status({ primary: 'local', local_logins: false, local_logins_setting: false }));
    wrap(<LoginProvidersPanel />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sync Jarvis now' }));
    await waitFor(() => expect(m.syncLoginProvider).toHaveBeenCalledWith('jarvis_identity'));
    fireEvent.change(screen.getByLabelText('Primary login'), { target: { value: 'local' } });
    await waitFor(() => expect(m.setLoginProviders).toHaveBeenCalledWith({ primary: 'local' }));
    fireEvent.click(screen.getByLabelText('Allow logins with hub passwords'));
    await waitFor(() => expect(m.setLoginProviders).toHaveBeenCalledWith({ local_logins: false }));
    expect(await screen.findByText('Only Jarvis accounts can log in')).toBeTruthy();
  });

  it('keeps hub passwords on when nothing else is set up, and adds a hub user', async () => {
    m.getLoginProviders.mockResolvedValue(status({ providers: [{ ...local, primary: true },
      { ...jarvis, configured: false, active: false, primary: false, last_sync: null }], primary: 'local',
      external_admins: [], standalone_users: [] }));
    m.createHubUser.mockResolvedValue(users[1]);
    wrap(<LoginProvidersPanel />);
    expect(await screen.findAllByTestId('login-provider')).toHaveLength(1);
    expect((screen.getByLabelText('Allow logins with hub passwords') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText('Always on while no other login is set up.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'New hub user' }));
    const form = screen.getByTestId('hub-user-editor');
    fireEvent.change(within(form).getByLabelText('Name'), { target: { value: ' Visitor ' } });
    fireEvent.change(within(form).getByLabelText('Username'), { target: { value: 'Visitor' } });
    fireEvent.change(within(form).getByLabelText('Password'), { target: { value: 'made-up-pass' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Add hub user' }));
    await waitFor(() => expect(m.createHubUser).toHaveBeenCalledWith(
      { name: 'Visitor', username: 'visitor', password: 'made-up-pass', is_admin: false }));
  });
});

describe('PersonsPanel with Jarvis people', () => {
  it('marks a Jarvis person and offers no edit there; a hub person stays editable', async () => {
    m.getPersons.mockResolvedValue({
      persons: [
        { id: 'p1', entity_id: 'person.robin', name: 'Robin', user_id: 'u1', jarvis_user: 'robin', device_trackers: [],
          state: 'home', source: 'jarvis_identity', editable: false },
        { id: 'p2', entity_id: 'person.guest', name: 'Guest', user_id: null, jarvis_user: null, device_trackers: [],
          state: null, source: 'hub', editable: true },
      ],
      trackers: [],
    });
    wrap(<PersonsPanel />);
    const rows = await screen.findAllByTestId('person');
    expect(within(rows[0]).getByTestId('person-source').textContent).toBe('From Jarvis');
    expect(within(rows[0]).queryByRole('button', { name: 'Edit Robin' })).toBeNull();
    expect(within(rows[0]).getByText(/change them in Jarvis/)).toBeTruthy();
    expect(within(rows[1]).queryByTestId('person-source')).toBeNull();
    expect(within(rows[1]).getByRole('button', { name: 'Edit Guest' })).toBeTruthy();
  });
});
