import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PhoneProtectionCard from './PhoneProtectionCard';
import Guard from '../../plugins/guard';
import PhoneUsage from '../../plugins/phoneUsage';
import MediaSession from '../../plugins/mediaSession';
import Comms from '../../plugins/comms';
import { api } from '../../services/api';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, Capacitor: { ...(actual.Capacitor as object), getPlatform: () => platform.name } };
});
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'kid' } }) }));
vi.mock('../../plugins/guard', () => ({ default: { status: vi.fn(), enableAdmin: vi.fn(), openGuardSettings: vi.fn(), openAppInfo: vi.fn() } }));
vi.mock('../../plugins/phoneUsage', () => ({ default: { hasAccess: vi.fn(), openSettings: vi.fn() } }));
vi.mock('../../plugins/mediaSession', () => ({ default: { hasAccess: vi.fn(), openSettings: vi.fn() } }));
vi.mock('../../plugins/comms', () => ({ default: { hasAccess: vi.fn(), requestAccess: vi.fn() } }));
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), getScreenTimeStatus: vi.fn() } };
});

const renderCard = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PhoneProtectionCard />
    </QueryClientProvider>,
  );

describe('PhoneProtectionCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    platform.name = 'android';
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue({ protected: true } as never);
    vi.mocked(PhoneUsage.hasAccess).mockResolvedValue({ granted: true });
    vi.mocked(MediaSession.hasAccess).mockResolvedValue({ granted: true });
    vi.mocked(Comms.hasAccess).mockResolvedValue({ calls: true, sms: true });
    vi.mocked(Comms.requestAccess).mockResolvedValue({ calls: true, sms: true });
  });

  it('asks for what the reports need when the phone has not allowed it', async () => {
    vi.mocked(Guard.status).mockResolvedValue({ adminActive: true, deviceOwner: false, guardEnabled: true });
    vi.mocked(PhoneUsage.hasAccess).mockResolvedValue({ granted: false });
    vi.mocked(MediaSession.hasAccess).mockResolvedValue({ granted: false });
    vi.mocked(Comms.hasAccess).mockResolvedValue({ calls: false, sms: false });
    renderCard();
    const usage = await screen.findByTestId('phone-protection-usage');
    expect(screen.queryByTestId('phone-protection-on')).toBeNull();
    fireEvent.click(usage.querySelector('button')!);
    expect(PhoneUsage.openSettings).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('phone-protection-notifications').querySelector('button')!);
    expect(MediaSession.openSettings).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(Comms.requestAccess).toHaveBeenCalled();
  });

  it('walks a parent through the two switches', async () => {
    vi.mocked(Guard.status).mockResolvedValue({ adminActive: false, deviceOwner: false, guardEnabled: false });
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: 'Turn on' }));
    expect(Guard.enableAdmin).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Open Accessibility' }));
    expect(Guard.openGuardSettings).toHaveBeenCalled();
  });

  it('says plainly when both are on', async () => {
    vi.mocked(Guard.status).mockResolvedValue({ adminActive: true, deviceOwner: false, guardEnabled: true });
    renderCard();
    expect(await screen.findByTestId('phone-protection-on')).toHaveTextContent('Jarvis is protected on this phone');
  });

  it('shows the one-time device-owner command when the phone is not one', async () => {
    vi.mocked(Guard.status).mockResolvedValue({ adminActive: true, deviceOwner: false, guardEnabled: true });
    renderCard();
    const block = await screen.findByTestId('phone-protection-device-owner');
    expect(block).toHaveTextContent('adb shell dpm set-device-owner com.jarvisos.app/.GuardAdminReceiver');
    expect(block).toHaveTextContent('no accounts signed in');
  });

  it('hides the device-owner guidance once the phone is a device owner', async () => {
    vi.mocked(Guard.status).mockResolvedValue({ adminActive: true, deviceOwner: true, guardEnabled: true });
    renderCard();
    await screen.findByTestId('phone-protection-on');
    expect(screen.queryByTestId('phone-protection-device-owner')).not.toBeInTheDocument();
  });

  it("is not shown on an adult's phone or off Android", async () => {
    vi.mocked(Guard.status).mockResolvedValue({ adminActive: false, deviceOwner: false, guardEnabled: false });
    vi.mocked(api.getScreenTimeStatus).mockResolvedValue({ protected: false } as never);
    const { container } = renderCard();
    await new Promise((r) => setTimeout(r, 30));
    expect(container).toBeEmptyDOMElement();
  });
});
