import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import AdoptDeviceDialog from './AdoptDeviceDialog';
import type { AdoptResult } from '../../lib/discoveredDevices';

const PIN = { key: 'pin', label: 'Code on the TV', type: 'text' as const };

function open(onSubmit: (v: { name?: string; input: Record<string, string> }) => Promise<AdoptResult>) {
  const onDone = vi.fn();
  render(<AdoptDeviceDialog title="Add Den TV" needs={[]} onSubmit={onSubmit} onDone={onDone} onClose={vi.fn()} />);
  return { onDone, dialog: screen.getByTestId('adopt-dialog') };
}

describe('AdoptDeviceDialog pairing steps', () => {
  it('asks for the code the TV shows, keeps the session, and retries a wrong code', async () => {
    const onSubmit = vi.fn()
      .mockResolvedValueOnce({ status: 'needs_input', needs: [PIN], message: 'Enter the code shown on the TV.', session: 's1' })
      .mockResolvedValueOnce({ status: 'needs_input', needs: [PIN], error: 'That code was not accepted.', session: 's1' })
      .mockResolvedValueOnce({ status: 'adopted', entry_id: 'e1', name: 'Den TV' });
    const { onDone, dialog } = open(onSubmit);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    const code = await within(dialog).findByLabelText(/Code on the TV/);
    expect(within(dialog).getByText('Enter the code shown on the TV.')).toBeInTheDocument();
    fireEvent.change(code, { target: { value: 'ABC123' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('That code was not accepted.');
    fireEvent.change(within(dialog).getByLabelText(/Code on the TV/), { target: { value: 'A1B2C3' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(onSubmit).toHaveBeenNthCalledWith(1, { name: undefined, input: {} });
    expect(onSubmit).toHaveBeenNthCalledWith(2, { name: undefined, input: { pin: 'ABC123', session: 's1' } });
    expect(onSubmit).toHaveBeenNthCalledWith(3, { name: undefined, input: { pin: 'A1B2C3', session: 's1' } });
  });

  it('waits while the TV asks to allow, polling with the session, then reports a refusal', async () => {
    const onSubmit = vi.fn()
      .mockResolvedValueOnce({ status: 'pending', session: 's2', step: 'accept_on_tv', message: 'Choose Allow.', poll_ms: 5 })
      .mockResolvedValueOnce({ status: 'pending', session: 's2', step: 'accept_on_tv', message: 'Choose Allow.', poll_ms: 5 })
      .mockResolvedValueOnce({ status: 'error', message: 'The TV refused the connection.' });
    const { dialog } = open(onSubmit);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    expect(await within(dialog).findByTestId('adopt-waiting')).toHaveTextContent('Accept on your TV');
    expect(within(dialog).queryByRole('button', { name: /Add|Continue/ })).toBeNull();
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('The TV refused the connection.');
    expect(onSubmit).toHaveBeenCalledTimes(3);
    expect(onSubmit).toHaveBeenLastCalledWith({ name: undefined, input: { session: 's2' } });
    // a fresh try starts a new pairing (no stale session)
    expect(within(dialog).getByRole('button', { name: 'Add' })).toBeInTheDocument();
  });

  it('a new pairing on the TV clears the old code', async () => {
    const onSubmit = vi.fn()
      .mockResolvedValueOnce({ status: 'needs_input', needs: [PIN], session: 's1' })
      .mockResolvedValueOnce({ status: 'needs_input', needs: [PIN], session: 's3', error: 'The TV closed the pairing; enter the new code it shows.' });
    const { dialog } = open(onSubmit);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    fireEvent.change(await within(dialog).findByLabelText(/Code on the TV/), { target: { value: 'OLD111' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('new code');
    expect(within(dialog).getByLabelText(/Code on the TV/)).toHaveValue('');
  });
});
