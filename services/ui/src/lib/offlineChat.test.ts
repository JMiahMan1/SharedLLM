import { describe, expect, it } from 'vitest';
import { base64Bytes, deliveryLine, mapLink } from './offlineChat';
import type { MeshMessage } from '../plugins/familyMesh';

const msg = (over: Partial<MeshMessage>): MeshMessage => ({
  id: 'a'.repeat(32), t: 'text', from: 'pat', room: 'abcd1234', sent_at: 1, mine: false, synced: false, hops: 1, ...over,
});

describe('offline chat', () => {
  it('says where a message is', () => {
    expect(deliveryLine(msg({ synced: true }))).toBe('In the family chat');
    expect(deliveryLine(msg({ mine: true }))).toMatch(/Waiting for the internet/);
    expect(deliveryLine(msg({ hops: 1 }))).toBe('From a phone nearby');
    expect(deliveryLine(msg({ hops: 2 }))).toBe('Came through 1 other phone');
    expect(deliveryLine(msg({ hops: 4 }))).toBe('Came through 3 other phones');
  });

  it('links a place and measures base64', () => {
    expect(mapLink(39.7392, -104.9903)).toBe('https://www.openstreetmap.org/?mlat=39.73920&mlon=-104.99030#map=17/39.73920/-104.99030');
    expect(base64Bytes(btoa('abcd'))).toBe(4);
    expect(base64Bytes(btoa('abcde'))).toBe(5);
  });
});
