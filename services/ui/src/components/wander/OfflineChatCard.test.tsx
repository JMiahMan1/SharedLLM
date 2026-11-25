import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { MeshDiagnostics, MeshMessage } from '../../plugins/familyMesh';

const platform = vi.hoisted(() => ({ name: 'android' }));
vi.mock('@capacitor/core', async (orig) => {
  const real = await orig<typeof import('@capacitor/core')>();
  return { ...real, Capacitor: { ...real.Capacitor, getPlatform: () => platform.name } };
});
const mesh = vi.hoisted(() => ({
  getDiagnostics: vi.fn(), enable: vi.fn(), disable: vi.fn(), setRoom: vi.fn(), send: vi.fn(), list: vi.fn(),
  addListener: vi.fn(),
}));
vi.mock('../../plugins/familyMesh', () => ({ default: mesh }));

import OfflineChatCard from './OfflineChatCard';

const on: MeshDiagnostics = {
  enabled: true, connectPermission: true, room: true, server: true, syncing: null, stored: 0, unsynced: 0,
  hasMail: false, lastSentAt: null, lastReceivedAt: null, lastServerSyncAt: null, lastError: null,
};
const message = (over: Partial<MeshMessage>): MeshMessage => ({
  id: 'a'.repeat(32), t: 'text', from: 'gracie', room: 'abcd1234', sent_at: 1, mine: false, synced: false, hops: 1, ...over,
});

describe('OfflineChatCard', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    platform.name = 'android';
    mesh.getDiagnostics.mockResolvedValue(on);
    mesh.list.mockResolvedValue({ messages: [] });
    mesh.addListener.mockResolvedValue({ remove: async () => undefined });
    mesh.setRoom.mockResolvedValue(undefined);
  });

  it('hands the phone the family chat and sends a message', async () => {
    mesh.send.mockResolvedValue({ id: 'x' });
    render(<OfflineChatCard me="pat" room="abcd1234" />);
    await screen.findByTestId('offline-chat-messages');
    expect(mesh.setRoom).toHaveBeenCalledWith({ room: 'abcd1234' });
    await userEvent.type(screen.getByLabelText('Offline message'), 'At the trailhead');
    await userEvent.click(screen.getByRole('button', { name: 'Send offline' }));
    expect(mesh.send).toHaveBeenCalledWith({ kind: 'text', fields: { text: 'At the trailhead' } });
    await waitFor(() => expect(screen.getByLabelText('Offline message')).toHaveValue(''));
  });

  it('shows texts, places and previews with where each one is', async () => {
    mesh.list.mockResolvedValue({ messages: [
      message({ text: 'Lost signal, all fine', hops: 2 }),
      message({ id: 'b'.repeat(32), t: 'pin', lat: 39.7392, lon: -104.9903, label: 'Trailhead', from: 'pat', mine: true }),
      message({ id: 'c'.repeat(32), t: 'thumb', data: 'AAAA', synced: true }),
    ] });
    render(<OfflineChatCard me="pat" room="abcd1234" nameOf={(u) => (u === 'gracie' ? 'Gracie' : undefined)} />);
    expect(await screen.findByText('Lost signal, all fine')).toBeInTheDocument();
    expect(screen.getByText('Came through 1 other phone')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Trailhead/ })).toHaveAttribute('href', expect.stringContaining('mlat=39.73920'));
    expect(screen.getByText(/Waiting for the internet/)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Photo preview' })).toBeInTheDocument();
    expect(screen.getByText('In the family chat')).toBeInTheDocument();
    expect(screen.getAllByText('Gracie').length).toBeGreaterThan(0);
  });

  it('shares a place only when the phone knows where it is', async () => {
    mesh.send.mockResolvedValue({ id: 'x' });
    const { rerender } = render(<OfflineChatCard me="pat" room="abcd1234" />);
    expect(await screen.findByRole('button', { name: 'Share my location offline' })).toBeDisabled();
    rerender(<OfflineChatCard me="pat" room="abcd1234" position={{ lat: 1, lon: 2 }} />);
    await userEvent.click(screen.getByRole('button', { name: 'Share my location offline' }));
    expect(mesh.send).toHaveBeenCalledWith({ kind: 'pin', fields: { lat: 1, lon: 2, label: 'Where I am' } });
  });

  it('says when there is no family chat to send to, or no permission', async () => {
    mesh.getDiagnostics.mockResolvedValue({ ...on, room: false, connectPermission: false });
    render(<OfflineChatCard me="pat" room={null} />);
    expect(await screen.findByTestId('offline-chat-room')).toHaveTextContent(/Choose the chat/);
    expect(screen.getByTestId('offline-chat-permission')).toHaveTextContent(/Nearby devices/);
  });

  it('is off until turned on, and not drawn in the browser', async () => {
    mesh.getDiagnostics.mockResolvedValue({ ...on, enabled: false });
    mesh.enable.mockResolvedValue(on);
    render(<OfflineChatCard me="pat" room="abcd1234" />);
    await userEvent.click(await screen.findByRole('button', { name: 'Turn on' }));
    expect(mesh.enable).toHaveBeenCalled();
    platform.name = 'web';
    const { container } = render(<OfflineChatCard me="pat" room="abcd1234" />);
    expect(container).toBeEmptyDOMElement();
  });
});
