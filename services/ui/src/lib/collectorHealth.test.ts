import { describe, expect, it } from 'vitest';
import { collectionNotices, UPLOAD_FAILING_MS, type CollectorHealth } from './collectorHealth';

const NOW = Date.UTC(2026, 10, 23, 18, 0);
const texts = (h: CollectorHealth) => collectionNotices(h, 'Robin', 'phone', NOW).map((n) => n.text);

describe('collectionNotices', () => {
  it('says nothing when all is well or nothing is known', () => {
    expect(collectionNotices(undefined, 'Robin')).toEqual([]);
    expect(texts({ devices: [{ device_id: 'd1', problems: [] }], staleness: { comms: { stale: false }, usage: { stale: false } } })).toEqual([]);
  });

  it('turns the server staleness check into a plain sentence with the date', () => {
    const out = texts({ staleness: { comms: { stale: true, last_at: '2026-11-21T19:00:00+00:00' }, usage: { stale: true, last_at: '2026-11-21T08:00:00+00:00' } } });
    expect(out).toEqual([
      "Robin's phone has not sent texts or calls since Nov 21. Open Jarvis on the phone to restart collection.",
      "Robin's phone has not sent screen time since Nov 21. Open Jarvis on the phone to restart collection.",
    ]);
  });

  it('says plainly that a tablet can only scan for family phones', () => {
    const out = collectionNotices({ devices: [{ device_id: 'd1', problems: [
      { kind: 'collector', what: 'proximity', outcome: 'cannot_advertise',
        message: 'This device cannot advertise over Bluetooth LE: it sees family phones, but they do not see it' },
    ] }] }, 'Robin', 'tablet', NOW).map((n) => n.text);
    expect(out).toEqual(["Family nearby on Robin's tablet: This device cannot advertise over Bluetooth LE: it sees family phones, but they do not see it."]);
  });

  it('names the missing permissions', () => {
    const out = texts({ devices: [{ device_id: 'd1', problems: [
      { kind: 'permission', what: 'read_sms' }, { kind: 'permission', what: 'read_call_log' },
    ] }] });
    expect(out).toEqual(["Robin's phone is missing permission for Text messages and Call log. Open Jarvis on the phone and allow them."]);
  });

  it('says background collection is off when a worker is not scheduled', () => {
    const out = texts({ devices: [{ device_id: 'd1', problems: [
      { kind: 'worker', what: 'comms_sync', state: 'CANCELLED' }, { kind: 'worker', what: 'usage_sync', state: 'NONE' },
    ] }] });
    expect(out).toEqual(["Background collection of calls and texts and screen time is not running on Robin's phone. Open Jarvis on the phone to restart collection."]);
  });

  it('says a signed-out phone is signed out, instead of listing every stopped worker', () => {
    const out = texts({ devices: [{ device_id: 'd1', problems: [
      { kind: 'worker', what: 'comms_sync', state: 'CANCELLED' },
      { kind: 'collector', what: 'comms', outcome: 'signed_out', message: 'Not signed in on this phone' },
    ] }] });
    expect(out).toEqual(["Robin's phone is signed out of Jarvis, so nothing is collected. Sign in on the phone."]);
  });

  it('reports a failing upload only once it has lasted hours', () => {
    const failing = (lastOk: number | null): CollectorHealth => ({ devices: [{
      device_id: 'd1',
      collectors: { comms: { ok: false, outcome: 'upload_failed', last_ok_at: lastOk } },
      problems: [
        { kind: 'collector', what: 'comms', outcome: 'upload_failed', message: 'Offline' },
        { kind: 'collector', what: 'listener', outcome: 'upload_failed', message: 'Offline' },
      ],
    }] });
    expect(texts(failing(NOW - 60_000))).toEqual([]);
    const out = texts(failing(NOW - UPLOAD_FAILING_MS - 1));
    expect(out).toHaveLength(1);
    expect(out[0]).toMatch(/^Robin's phone could not upload calls and texts since Nov 2\d \(Offline\)\.$/);
  });

  it('names the device when the child has more than one', () => {
    const out = collectionNotices({ devices: [
      { device_id: 'd1', device_name: 'Pixel', problems: [{ kind: 'permission', what: 'usage_access' }] },
      { device_id: 'd2', device_name: 'Tab', problems: [] },
    ] }, 'Robin', 'tablet', NOW).map((n) => n.text);
    expect(out).toEqual(["Robin's tablet (Pixel) is missing permission for Usage access (screen time). Open Jarvis on the tablet and allow it."]);
  });
});
