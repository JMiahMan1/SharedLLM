import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import MyVoicePanel from './MyVoicePanel';
import { api } from '../../services/api';

vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'pat', preferred_tts_voice: '' } }) }));
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal() as Record<string, unknown>;
  return { ...actual, api: { ...(actual.api as Record<string, unknown>), getTtsVoices: vi.fn(), setMyVoice: vi.fn(), ttsSample: vi.fn() } };
});

const renderPanel = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MyVoicePanel />
    </QueryClientProvider>,
  );

const gatewayError = (status: number, data: unknown) => Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data } });

describe('MyVoicePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the gateway's FAILURE message when the answer is not a voice list", async () => {
    vi.mocked(api.getTtsVoices).mockResolvedValue({ status: 'FAILURE', message: 'Kokoro model files are missing' } as never);
    renderPanel();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The server did not send a list of voices: Kokoro model files are missing');
  });

  it('says plainly when a FAILURE body gives no reason', async () => {
    vi.mocked(api.getTtsVoices).mockResolvedValue({ status: 'FAILURE' } as never);
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('it gave no reason');
  });

  it("shows the gateway's error text when loading the voices fails", async () => {
    vi.mocked(api.getTtsVoices).mockRejectedValue(gatewayError(502, { detail: 'The speech engine is not running' }));
    renderPanel();
    expect(await screen.findByRole('alert')).toHaveTextContent('The voices could not be loaded: The speech engine is not running');
  });

  it("shows the gateway's error text when saving or playing fails", async () => {
    vi.mocked(api.getTtsVoices).mockResolvedValue({ status: 'SUCCESS', voices: ['af_heart'], featured: [], default: 'af_heart' } as never);
    vi.mocked(api.setMyVoice).mockRejectedValue(gatewayError(400, { detail: 'Unknown voice' }));
    vi.mocked(api.ttsSample).mockRejectedValue(gatewayError(502, { status: 'FAILURE', message: 'Synthesis timed out' }));
    renderPanel();
    const select = await screen.findByTestId('my-voice-select');
    await vi.waitFor(() => expect(select).not.toBeDisabled());
    fireEvent.change(select, { target: { value: 'af_heart' } });
    await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith('Your voice was not saved: Unknown voice'));
    fireEvent.click(screen.getByTestId('my-voice-play'));
    await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith('That voice could not be played: Synthesis timed out'));
  });
});
