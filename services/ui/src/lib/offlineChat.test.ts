import { describe, expect, it } from 'vitest';
import { base64Bytes, deliveryLine, mapLink, roomOfflineMessages } from './offlineChat';
import type { MeshMessage } from '../plugins/familyMesh';

const msg = (over: Partial<MeshMessage>): MeshMessage => ({
  id: 'a'.repeat(32), t: 'text', from: 'pat', room: 'abcd1234', sent_at: 1, mine: false, synced: false, hops: 1, ...over,
});

describe('offline chat', () => {
  it('says where a message is', () => {
    expect(deliveryLine(msg({ synced: true }))).toBe('In the chat');
    expect(deliveryLine(msg({ mine: true }))).toMatch(/Waiting for the internet/);
    expect(deliveryLine(msg({ hops: 1 }))).toBe('From a phone nearby');
    expect(deliveryLine(msg({ hops: 2 }))).toBe('Came through 1 other phone');
    expect(deliveryLine(msg({ hops: 4 }))).toBe('Came through 3 other phones');
  });

  it('shows each message only in its own room, and online only what the chat does not have yet', () => {
    const list = [
      msg({ id: '1'.repeat(32), room: 'abcd1234' }),
      msg({ id: '2'.repeat(32), room: 'grp98765' }),
      msg({ id: '3'.repeat(32), room: 'abcd1234', synced: true }),
    ];
    expect(roomOfflineMessages(list, 'abcd1234', true).map((m) => m.id[0])).toEqual(['1', '3']);
    expect(roomOfflineMessages(list, 'abcd1234', false).map((m) => m.id[0])).toEqual(['1']);
    expect(roomOfflineMessages(list, 'grp98765', false).map((m) => m.id[0])).toEqual(['2']);
    expect(roomOfflineMessages(list, 'other999', true)).toEqual([]);
  });

  it('links a place and measures base64', () => {
    expect(mapLink(39.7392, -104.9903)).toBe('https://www.openstreetmap.org/?mlat=39.73920&mlon=-104.99030#map=17/39.73920/-104.99030');
    expect(base64Bytes(btoa('abcd'))).toBe(4);
    expect(base64Bytes(btoa('abcde'))).toBe(5);
  });
});
