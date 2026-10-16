import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SafetyButtons from './SafetyButtons';
import { api } from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    api: { ...(actual.api as Record<string, unknown>), sendOnMyWay: vi.fn(), sendSos: vi.fn() },
  };
});

const sendOnMyWay = vi.mocked(api.sendOnMyWay);
const sendSos = vi.mocked(api.sendSos);

describe('SafetyButtons', () => {
  beforeEach(() => vi.resetAllMocks());

  it('tells the family you are on the way in one tap', async () => {
    sendOnMyWay.mockResolvedValue({ status: 'SUCCESS', sent: 2, eta_s: 1260 });
    render(<SafetyButtons />);
    await userEvent.click(screen.getByRole('button', { name: /on my way/i }));
    await waitFor(() => expect(sendOnMyWay).toHaveBeenCalledTimes(1));
  });

  it('asks before sending an SOS, and Cancel sends nothing', async () => {
    render(<SafetyButtons />);
    await userEvent.click(screen.getByRole('button', { name: /^sos$/i }));
    const dialog = await screen.findByRole('dialog', { name: /send an sos/i });
    expect(dialog).toHaveTextContent(/even if they turned family alerts off/i);
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(sendSos).not.toHaveBeenCalled();
  });

  it('sends the SOS once confirmed', async () => {
    sendSos.mockResolvedValue({ status: 'SUCCESS', sent: 2, family: 2, located: true });
    render(<SafetyButtons />);
    await userEvent.click(screen.getByRole('button', { name: /^sos$/i }));
    await userEvent.click(await screen.findByRole('button', { name: /send sos/i }));
    await waitFor(() => expect(sendSos).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('keeps the buttons a thumb wide', () => {
    render(<SafetyButtons />);
    for (const name of [/on my way/i, /^sos$/i]) {
      expect(screen.getByRole('button', { name }).className).toMatch(/min-h-11/);
    }
  });
});
