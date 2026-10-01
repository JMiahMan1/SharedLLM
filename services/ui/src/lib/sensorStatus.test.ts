import { describe, it, expect } from 'vitest';
import {
  sensorStatus,
  isHealthy,
  worstOf,
  type SensorStatusInput,
} from './sensorStatus';

const healthy: SensorStatusInput = {
  enabled: true,
  permission: 'granted',
  message: null,
  recovering: false,
};

const enabled = (over: Partial<SensorStatusInput> = {}): SensorStatusInput => ({
  ...healthy,
  ...over,
});

describe('sensorStatus', () => {
  it('reports a healthy sensor as ok so the banner can render nothing', () => {
    const view = sensorStatus('steps', healthy);
    expect(view.health).toBe('ok');
    expect(isHealthy(view)).toBe(true);
    expect(view.title).toBe('');
  });

  it('treats a recovering-but-enabled sensor as recovering, not off', () => {
    // The regression this guards: recovery outranks `enabled`, otherwise a
    // sensor that is retrying but still switched on reads as simply "off".
    const view = sensorStatus('steps', enabled({ recovering: true }));
    expect(view.health).toBe('recovering');
    expect(view.canEnable).toBe(false);
  });

  it('routes a denial to the OS settings rather than offering a retry', () => {
    const view = sensorStatus('location', {
      enabled: false,
      permission: 'denied',
      message: null,
      recovering: false,
    });
    expect(view.health).toBe('denied');
    expect(view.needsOsSettings).toBe(true);
    expect(view.canEnable).toBe(false);
  });

  it('offers a one-tap re-enable for a sensor that is merely off', () => {
    const view = sensorStatus('steps', {
      enabled: false,
      permission: 'granted',
      message: null,
      recovering: false,
    });
    expect(view.health).toBe('off');
    expect(view.canEnable).toBe(true);
  });

  it('reports no action at all when the hardware is unavailable', () => {
    const view = sensorStatus('steps', {
      enabled: false,
      permission: 'unavailable',
      message: 'No hardware step counter',
      recovering: false,
    });
    expect(view.health).toBe('unavailable');
    expect(view.canEnable).toBe(false);
    expect(view.needsOsSettings).toBe(false);
    expect(view.detail).toContain('No hardware step counter');
  });

  it('unavailable outranks denied so missing hardware is never reported as a permission problem', () => {
    const view = sensorStatus('steps', {
      enabled: false,
      permission: 'unavailable',
      message: null,
      recovering: false,
    });
    expect(view.health).toBe('unavailable');
  });

  it('denial outranks recovering so the actionable problem is the one shown', () => {
    const view = sensorStatus('location', {
      enabled: false,
      permission: 'denied',
      message: null,
      recovering: true,
    });
    expect(view.health).toBe('denied');
  });

  it('names the data consequence per sensor, not a generic string', () => {
    const off = { enabled: false, permission: 'granted', message: null, recovering: false } as const;
    expect(sensorStatus('location', off).detail).toMatch(/location/i);
    expect(sensorStatus('steps', off).detail).toMatch(/steps/i);
    expect(sensorStatus('location', off).detail).not.toBe(sensorStatus('steps', off).detail);
  });

  it('prefers the real message over the generic fallback when one exists', () => {
    const view = sensorStatus('location', {
      enabled: false,
      permission: 'granted',
      message: 'Waiting for GPS fix…',
      recovering: false,
    });
    expect(view.detail).toBe('Waiting for GPS fix…');
  });

  it('degrades to a safe generic label for an unknown sensor id', () => {
    const view = sensorStatus('something_new', {
      enabled: false,
      permission: 'granted',
      message: null,
      recovering: false,
    });
    expect(view.health).toBe('off');
    expect(view.title).toContain('Tracking');
  });
});

describe('worstOf', () => {
  it('returns null when every sensor is healthy', () => {
    expect(worstOf([sensorStatus('steps', healthy), sensorStatus('location', healthy)])).toBeNull();
  });

  it('picks the denial over a merely-off sensor', () => {
    const denied = sensorStatus('location', {
      enabled: false,
      permission: 'denied',
      message: null,
      recovering: false,
    });
    const off = sensorStatus('steps', {
      enabled: false,
      permission: 'granted',
      message: null,
      recovering: false,
    });
    expect(worstOf([off, denied])?.health).toBe('denied');
    expect(worstOf([denied, off])?.health).toBe('denied');
  });

  it('prefers recovering over off — a live retry needs less attention than a dead switch', () => {
    const recovering = sensorStatus('steps', enabled({ recovering: true }));
    const off = sensorStatus('location', {
      enabled: false,
      permission: 'granted',
      message: null,
      recovering: false,
    });
    expect(worstOf([recovering, off])?.health).toBe('recovering');
  });

  it('ignores healthy sensors when choosing the worst problem', () => {
    const off = sensorStatus('steps', {
      enabled: false,
      permission: 'granted',
      message: null,
      recovering: false,
    });
    expect(worstOf([sensorStatus('location', healthy), off])?.health).toBe('off');
  });

  it('handles an empty list without throwing', () => {
    expect(worstOf([])).toBeNull();
  });
});