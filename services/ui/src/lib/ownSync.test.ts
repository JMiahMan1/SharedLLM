import { describe, expect, it } from 'vitest';
import { OWN_SYNC_CATEGORIES, peerName, shortWhen, statusText } from './ownSync';

describe('ownSync', () => {
  const at = new Date(2026, 10, 25, 15, 42).getTime();

  it('says In sync, or which device a newer copy came from and when', () => {
    expect(statusText({ status: 'in_sync', at })).toBe('In sync');
    expect(statusText({ status: 'updated', from: 'Test Tablet', at })).toBe(`Updated from Test Tablet at ${shortWhen(at)}`);
    expect(statusText({ status: 'updated', at })).toMatch(/^Updated from your other device at /);
    expect(statusText(undefined)).toBe('Not synced yet');
  });

  it('lists every kind of data a sync carries, once', () => {
    const ids = OWN_SYNC_CATEGORIES.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toEqual(['device', 'steps', 'watch', 'rooms', 'family', 'chat', 'requests', 'guardian']);
  });

  it('names a device for people, never by its id', () => {
    expect(peerName({ device: 'abc', name: '  ', last_sync_at: at, types: {} })).toBe('Your other device');
    expect(peerName({ device: 'abc', name: 'Test Tablet', last_sync_at: at, types: {} })).toBe('Test Tablet');
    expect(shortWhen(null)).toBe('never');
  });
});
