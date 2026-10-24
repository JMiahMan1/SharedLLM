import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HealthConnectCard from './HealthConnectCard';
import HealthConnect, { type HealthConnectStatus } from '../../plugins/healthConnect';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, Capacitor: { ...(actual.Capacitor as object), getPlatform: () => platform.name } };
});
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'kate' } }) }));
vi.mock('../../plugins/healthConnect', () => ({
  default: {
    status: vi.fn(),
    connect: vi.fn(),
    syncNow: vi.fn(),
    disconnect: vi.fn(),
    openHealthConnect: vi.fn(),
  },
}));

const base: HealthConnectStatus = {
  availability: 'available',
  connected: false,
  owner: null,
  granted: [],
  backgroundAvailable: true,
  backgroundGranted: false,
  lastSync: null,
  lastResult: null,
};

const renderCard = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <HealthConnectCard />
    </QueryClientProvider>,
  );

describe('HealthConnectCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    platform.name = 'android';
  });

  it('connects for the signed-in person', async () => {
    vi.mocked(HealthConnect.status).mockResolvedValue(base);
    vi.mocked(HealthConnect.connect).mockResolvedValue({
      ...base,
      connected: true,
      owner: 'kate',
      granted: ['sleep', 'weight'],
      lastSync: Date.now(),
      lastResult: { ok: true, sleep: { added: 3, duplicates: 0 }, metrics: { added: 2, duplicates: 0 } },
    });
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect Health Connect' }));
    expect(HealthConnect.connect).toHaveBeenCalledWith({ username: 'kate' });
    expect(await screen.findByTestId('health-connect-on')).toHaveTextContent('Importing sleep, weight from Health Connect');
    expect(screen.getByTestId('health-connect-last')).toHaveTextContent('5 new readings');
  });

  it('says why a sync could not run instead of pretending', async () => {
    vi.mocked(HealthConnect.status).mockResolvedValue({
      ...base,
      connected: true,
      owner: 'kate',
      granted: ['sleep'],
      lastResult: { ok: false, error: 'waiting_for_app' },
    });
    renderCard();
    expect(await screen.findByTestId('health-connect-last')).toHaveTextContent('Waiting for the app to be opened');
    expect(screen.getByText(/Background reading is off/)).toBeInTheDocument();
  });

  it('disconnects and withdraws access', async () => {
    vi.mocked(HealthConnect.status).mockResolvedValue({ ...base, connected: true, owner: 'kate', granted: ['sleep'] });
    vi.mocked(HealthConnect.disconnect).mockResolvedValue(base);
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect' }));
    expect(HealthConnect.disconnect).toHaveBeenCalledWith({ revoke: true });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect Health Connect' })).toBeInTheDocument());
  });

  it('offers the install when Health Connect is missing', async () => {
    vi.mocked(HealthConnect.status).mockResolvedValue({ ...base, availability: 'update_required' });
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: 'Get Health Connect' }));
    expect(HealthConnect.openHealthConnect).toHaveBeenCalled();
  });

  it('is not shown off Android', async () => {
    platform.name = 'web';
    vi.mocked(HealthConnect.status).mockResolvedValue(base);
    const { container } = renderCard();
    await new Promise((r) => setTimeout(r, 30));
    expect(container).toBeEmptyDOMElement();
    expect(HealthConnect.status).not.toHaveBeenCalled();
  });
});
