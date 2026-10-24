import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AskForSiteCard from './AskForSiteCard';
import { siteOf } from '../../lib/guardRequests';
import { api } from '../../services/api';
import type { ScreenTimeStatus } from '../../types/api';

vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'kid' } }) }));
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), getScreenTimeStatus: vi.fn(), getGuardRequests: vi.fn(), askAParent: vi.fn() },
  };
});

const status = (filterOn: boolean, protectedPhone = true) =>
  ({ protected: protectedPhone, rules: { web_filter: { enabled: filterOn } } }) as unknown as ScreenTimeStatus;

const wrap = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AskForSiteCard />
    </QueryClientProvider>,
  );

describe('AskForSiteCard (a child)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getGuardRequests).mockResolvedValue({ requests: [] });
    vi.mocked(api.askAParent).mockResolvedValue({} as never);
  });

  it('turns what was typed into the site a parent would allow', () => {
    expect(siteOf(' https://www.Minecraft.net/en-us/login ')).toBe('minecraft.net');
    expect(siteOf('docs.google.com?x=1')).toBe('docs.google.com');
  });

  it('asks a parent about a site with a note', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status(true));
    wrap();
    fireEvent.change(await screen.findByLabelText('Which website?'), { target: { value: 'https://www.minecraft.net/' } });
    fireEvent.change(screen.getByLabelText('What is the website for?'), { target: { value: 'school project' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask a parent' }));
    await waitFor(() => expect(api.askAParent).toHaveBeenCalledWith('site', 'minecraft.net', 'school project'));
  });

  it('shows only the child\'s own waiting site requests', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status(true));
    const base = { label: '', note: '', status: 'pending' as const, until: null, decided_by: null, created_at: 'x', decided_at: null };
    vi.mocked(api.getGuardRequests).mockResolvedValue({
      requests: [
        { ...base, id: 1, username: 'kid', kind: 'site', target: 'minecraft.net' },
        { ...base, id: 2, username: 'kid', kind: 'app', target: 'com.roblox.client' },
      ],
    });
    wrap();
    const waiting = await screen.findByTestId('ask-for-site-waiting');
    expect(waiting).toHaveTextContent('Asked about minecraft.net');
    expect(waiting).not.toHaveTextContent('roblox');
  });

  it('is not shown when the phone does not filter websites', async () => {
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue(status(false));
    wrap();
    await waitFor(() => expect(api.getScreenTimeStatus).toHaveBeenCalled());
    expect(screen.queryByTestId('ask-for-site')).toBeNull();
  });
});
