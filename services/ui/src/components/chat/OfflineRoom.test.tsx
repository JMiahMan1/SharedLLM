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
  getDiagnostics: vi.fn(), enable: vi.fn(), disable: vi.fn(), rooms: vi.fn(), refreshRooms: vi.fn(), send: vi.fn(), list: vi.fn(),
  addListener: vi.fn(),
}));
vi.mock('../../plugins/familyMesh', () => ({ default: mesh }));

import { OfflineChatNotice, OfflineRoomComposer, OfflineRoomMessages } from './OfflineRoom';
import { useOfflineChat } from './useOfflineChat';

const on: MeshDiagnostics = {
  enabled: true, connectPermission: true, rooms: 2, server: true, syncing: null, stored: 0, unsynced: 0,
  hasMail: false, lastSentAt: null, lastReceivedAt: null, lastServerSyncAt: null, lastError: null,
};
const message = (over: Partial<MeshMessage>): MeshMessage => ({
  id: 'a'.repeat(32), t: 'text', from: 'robin', room: 'abcd1234', sent_at: 1, mine: false, synced: false, hops: 1, ...over,
});

function Notice() {
  const chat = useOfflineChat();
  return <OfflineChatNotice chat={chat} />;
}

describe('offline chat in every room', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    platform.name = 'android';
    mesh.getDiagnostics.mockResolvedValue(on);
    mesh.rooms.mockResolvedValue({ rooms: [{ room: 'abcd1234', name: 'Robin', type: 1 }] });
    mesh.list.mockResolvedValue({ messages: [] });
    mesh.addListener.mockResolvedValue({ remove: async () => undefined });
  });

  it('a private one-to-one room sends to its own room, with no default room anywhere', async () => {
    mesh.send.mockResolvedValue({ id: 'x' });
    const sent = vi.fn();
    render(<OfflineRoomComposer room="abcd1234" ready onSent={sent} />);
    await userEvent.type(screen.getByLabelText('Offline message'), 'At the trailhead');
    await userEvent.click(screen.getByRole('button', { name: 'Send offline' }));
    expect(mesh.send).toHaveBeenCalledWith({ room: 'abcd1234', kind: 'text', fields: { text: 'At the trailhead' } });
    await waitFor(() => expect(screen.getByLabelText('Offline message')).toHaveValue(''));
    expect(sent).toHaveBeenCalled();
  });

  it('a group room sends to the group, and unsent words stay with their room', async () => {
    mesh.send.mockResolvedValue({ id: 'x' });
    const { rerender } = render(<OfflineRoomComposer room="grp98765" ready />);
    await userEvent.type(screen.getByLabelText('Offline message'), 'Half written');
    rerender(<OfflineRoomComposer room="abcd1234" ready />);
    expect(screen.getByLabelText('Offline message')).toHaveValue('');
    rerender(<OfflineRoomComposer room="grp98765" ready />);
    await userEvent.type(screen.getByLabelText('Offline message'), 'Camp at the lake');
    await userEvent.click(screen.getByRole('button', { name: 'Send offline' }));
    expect(mesh.send).toHaveBeenCalledWith({ room: 'grp98765', kind: 'text', fields: { text: 'Camp at the lake' } });
  });

  it('says so when a room has no key on this phone yet, and a refusal is shown', async () => {
    const { rerender } = render(<OfflineRoomComposer room="new12345" ready={false} />);
    expect(screen.getByTestId('offline-room-not-ready')).toHaveTextContent('Open Jarvis once with the internet on');
    expect(screen.queryByLabelText('Offline message')).toBeNull();
    mesh.send.mockRejectedValue(new Error('This chat is not ready to use offline yet.'));
    rerender(<OfflineRoomComposer room="abcd1234" ready />);
    await userEvent.type(screen.getByLabelText('Offline message'), 'hi');
    await userEvent.click(screen.getByRole('button', { name: 'Send offline' }));
    await waitFor(() => expect(screen.getByLabelText('Offline message')).toHaveValue('hi'));
  });

  it('shows texts, places and previews with where each one is', () => {
    render(
      <OfflineRoomMessages
        me="pat"
        nameOf={(u) => (u === 'robin' ? 'Robin' : undefined)}
        messages={[
          message({ text: 'Lost signal, all fine', hops: 2 }),
          message({ id: 'b'.repeat(32), t: 'pin', lat: 39.7392, lon: -104.9903, label: 'Trailhead', from: 'pat', mine: true, hops: 0 }),
          message({ id: 'c'.repeat(32), t: 'thumb', data: 'AAAA', synced: true }),
          message({ id: 'd'.repeat(32), text: 'Next to you', hops: 1 }),
        ]}
      />,
    );
    expect(screen.getByText('Lost signal, all fine')).toBeInTheDocument();
    expect(screen.getAllByText('Robin').length).toBeGreaterThan(0);
    expect(screen.getByText('You')).toBeInTheDocument();
    expect(screen.getByText('Came through 1 other phone')).toBeInTheDocument();
    expect(screen.getByText('From a phone nearby')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Trailhead/ })).toHaveAttribute('href', expect.stringContaining('mlat=39.73920'));
    expect(screen.getByText(/Waiting for the internet/)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Photo preview' })).toBeInTheDocument();
    expect(screen.getByText('In the chat')).toBeInTheDocument();
  });

  it('draws nothing for a room with no offline messages', () => {
    const { container } = render(<OfflineRoomMessages me="pat" messages={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('the inbox notice names no default room and warns when nothing is ready', async () => {
    mesh.rooms.mockResolvedValue({ rooms: [] });
    mesh.getDiagnostics.mockResolvedValue({ ...on, rooms: 0, connectPermission: false });
    render(<Notice />);
    expect(await screen.findByTestId('chat-offline')).toHaveTextContent('Open any chat');
    expect(screen.getByTestId('offline-chat-no-rooms')).toBeInTheDocument();
    expect(screen.getByTestId('offline-chat-permission')).toBeInTheDocument();
    expect(screen.getByTestId('chat-offline')).not.toHaveTextContent(/launch|default/i);
  });

  it('shows nothing when the offline chat is off or off Android', async () => {
    mesh.getDiagnostics.mockResolvedValue({ ...on, enabled: false });
    const { container, unmount } = render(<Notice />);
    await waitFor(() => expect(mesh.getDiagnostics).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    platform.name = 'web';
    const web = render(<Notice />);
    expect(web.container).toBeEmptyDOMElement();
  });
});
