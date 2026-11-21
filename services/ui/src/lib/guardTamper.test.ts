import { describe, it, expect } from 'vitest';
import { tamperText } from './guardTamper';

describe('tamperText', () => {
  it('names the way around the web filter and what it was', () => {
    expect(tamperText({ event: 'bypass_app', detail: 'Tor Browser' })).toBe(
      'An app that gets around the web filter (closed while it is on): Tor Browser',
    );
    expect(tamperText({ event: 'boot_without_guard', detail: '2 starts' })).toContain('safe mode');
  });

  it("names the family PC's reports: whose offline code, how many wrong codes", () => {
    expect(tamperText({ event: 'offline_unlock', detail: 'parent-a' })).toBe(
      "Unlocked with a parent's offline code: parent-a",
    );
    expect(tamperText({ event: 'offline_code_wrong', detail: '3 wrong codes' })).toBe(
      'Wrong offline unlock codes were typed in: 3 wrong codes',
    );
    expect(tamperText({ event: 'tray_missing' })).toContain('not counted');
  });

  it('keeps the plain words when there is no detail, and the raw event when it is new', () => {
    expect(tamperText({ event: 'filter_disabled' })).toBe("Jarvis's web filter was turned off");
    expect(tamperText({ event: 'something_new' })).toBe('something_new');
    expect(tamperText(undefined)).toBe('Protection changed');
  });
});
