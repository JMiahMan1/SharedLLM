import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DevicesSection from './DevicesSection';
import { api } from '../../../services/api';

vi.mock('../../ui/confirmDialog', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }));
vi.mock('../../../services/api', async (orig) => {
  const actual = (await orig()) as { api: Record<string, unknown> };
  return { api: { ...actual.api, getMemberDevices: vi.fn(), clearMemberTamper: vi.fn(), getAppUpdateOffer: vi.fn().mockResolvedValue({ apk_available: true, apk_version_code: 700 }) } };
});

const report = (i: number) => ({ id: i, event: 'guard_tamper', at: new Date(Date.now() - i * 60_000).toISOString(), extra: { event: 'guard_disabled' }, device_key: null });

function setup(tamper: ReturnType<typeof report>[]) {
  vi.mocked(api.getMemberDevices).mockResolvedValue({
    username: 'kid',
    devices: [{ device_key: 'kid-phone-0000000001', kind: 'phone', capabilities: {} }],
    tamper,
    offline_code: {},
  } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DevicesSection username="kid" name="Kid" />
    </QueryClientProvider>,
  );
}

describe('tamper reports', () => {
  beforeEach(() => vi.mocked(api.clearMemberTamper).mockReset());

  it('shows the latest five, all on request, and clears them', async () => {
    setup(Array.from({ length: 12 }, (_, i) => report(i)));
    const card = await screen.findByTestId('member-tamper');
    expect(within(card).getAllByRole('listitem')).toHaveLength(5);
    await userEvent.click(within(card).getByTestId('member-tamper-more'));
    expect(within(card).getAllByRole('listitem')).toHaveLength(12);

    vi.mocked(api.clearMemberTamper).mockResolvedValue({ cleared: 12 });
    vi.mocked(api.getMemberDevices).mockResolvedValue({ username: 'kid', devices: [{ device_key: 'kid-phone-0000000001', kind: 'phone', capabilities: {} }], tamper: [], offline_code: {} } as never);
    await userEvent.click(within(card).getByTestId('member-tamper-clear'));
    expect(api.clearMemberTamper).toHaveBeenCalledWith('kid');
    await waitFor(() => expect(screen.getByTestId('member-tamper-empty')).toBeInTheDocument());
  });

  it('has nothing to clear when there are no reports', async () => {
    setup([]);
    await screen.findByTestId('member-tamper-empty');
    expect(screen.queryByTestId('member-tamper-clear')).toBeNull();
  });
});

describe('a device that is not updating', () => {
  function withDevice(device: Record<string, unknown>) {
    vi.mocked(api.getMemberDevices).mockResolvedValue({ username: 'kid', devices: [device], tamper: [], offline_code: {} } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <DevicesSection username="kid" name="Kid" />
      </QueryClientProvider>,
    );
  }

  it('is flagged when it is several builds behind, with how to fix it', async () => {
    withDevice({ device_key: 'kid-tablet-000000001', kind: 'tablet', app_build: '690', capabilities: { guard: { installUpdates: false } } });
    const note = await screen.findByTestId('member-device-behind');
    expect(note).toHaveTextContent('10 builds behind (build 690, latest 700)');
    expect(note).toHaveTextContent('Allow from this source');
  });

  it('is not flagged when it is nearly current', async () => {
    withDevice({ device_key: 'kid-phone-0000000001', kind: 'phone', app_build: '699', capabilities: {} });
    await screen.findByTestId('member-device-kid-phone-0000000001');
    expect(screen.queryByTestId('member-device-behind')).toBeNull();
  });
});

describe('last location', () => {
  it('shows when the device last sent its location, separately from the app being opened', async () => {
    vi.mocked(api.getMemberDevices).mockResolvedValue({
      username: 'kid',
      devices: [{ device_key: 'kid-phone-0000000001', kind: 'phone', capabilities: {}, last_location_at: new Date(Date.now() - 5 * 60_000).toISOString() }],
      tamper: [],
      offline_code: {},
    } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <DevicesSection username="kid" name="Kid" />
      </QueryClientProvider>,
    );
    const card = await screen.findByTestId('member-device-kid-phone-0000000001');
    expect(within(card).getByText('Last location')).toBeInTheDocument();
    expect(within(card).queryByText('none yet')).toBeNull();
  });
});

describe('offline unlock codes', () => {
  function withCodes(offline_code: Record<string, boolean>) {
    vi.mocked(api.getMemberDevices).mockResolvedValue({
      username: 'kid',
      devices: [{ device_key: 'kid-tablet-000000001', kind: 'tablet', capabilities: {} }],
      tamper: [],
      offline_code,
    } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <DevicesSection username="kid" name="Kid" />
      </QueryClientProvider>,
    );
  }

  it('says plainly when no parent has set one', async () => {
    withCodes({ 'parent-a': false, 'parent-b': false });
    const card = await screen.findByTestId('member-offline-codes');
    expect(within(card).getByTestId('member-offline-codes-none')).toHaveTextContent('No parent has set an offline code yet');
    expect(within(card).getAllByText('not set')).toHaveLength(2);
    expect(within(card).queryByTestId('member-offline-codes-note')).toBeNull();
  });

  it('says devices pick a code up when online once one is set', async () => {
    withCodes({ 'parent-a': true, 'parent-b': false });
    const card = await screen.findByTestId('member-offline-codes');
    expect(within(card).getByTestId('member-offline-codes-note')).toHaveTextContent('the next time they are online');
    expect(within(card).queryByTestId('member-offline-codes-none')).toBeNull();
  });
});
