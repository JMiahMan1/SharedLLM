import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PhoneProtectionCard from './PhoneProtectionCard';
import Guard from '../../plugins/guard';
import { api } from '../../services/api';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, Capacitor: { ...(actual.Capacitor as object), getPlatform: () => platform.name } };
});
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'kid' } }) }));
vi.mock('../../plugins/guard', () => ({ default: { status: vi.fn(), enableAdmin: vi.fn(), openGuardSettings: vi.fn(), openAppInfo: vi.fn() } }));
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
