import { describe, expect, it } from 'vitest';
import {
  classifyLocationFailure,
  classifyStepFailure,
  isStepPermissionDenied,
  isUnsupportedAvailability,
  nextRetryDelayMs,
  sensorErrorMessage,
  shouldRetry,
} from './sensorFailure';

describe('classifyLocationFailure', () => {
  it('treats the coded PERMISSION_DENIED error as an explicit denial', () => {
    expect(classifyLocationFailure({ code: 1, message: 'User denied Geolocation' })).toBe('denied');
  });

  it('treats POSITION_UNAVAILABLE and TIMEOUT as transient, not denials', () => {
    expect(classifyLocationFailure({ code: 2, message: 'Position unavailable' })).toBe('transient');
    expect(classifyLocationFailure({ code: 3, message: 'Timeout expired' })).toBe('transient');
  });

  it('does NOT let an uncoded message claiming a permission problem disable the sensor', () => {
    // This is the regression that made tracking die permanently: the old code
    // scanned the text for "permission" and persisted a disable.
    const err = new Error('Activity Recognition permission bridge timed out');
    expect(classifyLocationFailure(err)).toBe('transient');
    expect(classifyLocationFailure(new Error('permission service unavailable'))).toBe('transient');
    expect(classifyLocationFailure(new Error('Access denied by the platform bridge'))).toBe('transient');
  });

  it('treats any other failure as transient so the app can self-heal', () => {
    expect(classifyLocationFailure(new Error('boom'))).toBe('transient');
    expect(classifyLocationFailure(undefined)).toBe('transient');
    expect(classifyLocationFailure(null)).toBe('transient');
    expect(classifyLocationFailure('denied')).toBe('transient');
  });

  it('ignores a non-numeric code rather than guessing', () => {
    expect(classifyLocationFailure({ code: 'PERMISSION_DENIED' })).toBe('transient');
    expect(classifyLocationFailure({ code: 1.5 })).toBe('transient');
  });
});

describe('classifyStepFailure', () => {
  it('always returns transient for every input, including permission-sounding text', () => {
    const inputs: unknown[] = [
      new Error('permission denied'),
      new Error('Physical activity permission denied'),
      { code: 1 },
      { granted: false },
      undefined,
      null,
      'denied',
      42,
    ];
    for (const input of inputs) {
      expect(classifyStepFailure(input)).toBe('transient');
    }
  });
});

describe('isStepPermissionDenied', () => {
  it('reads the structured result, not a message', () => {
    expect(isStepPermissionDenied({ granted: false })).toBe(true);
    expect(isStepPermissionDenied({ granted: true })).toBe(false);
  });

  it('treats a missing result as not denied, so nothing disables itself', () => {
    expect(isStepPermissionDenied(null)).toBe(false);
    expect(isStepPermissionDenied(undefined)).toBe(false);
  });
});

describe('isUnsupportedAvailability', () => {
  it('detects hardware that cannot provide steps at all', () => {
    expect(isUnsupportedAvailability({ available: false })).toBe(true);
    expect(isUnsupportedAvailability({ available: true })).toBe(false);
    expect(isUnsupportedAvailability(null)).toBe(false);
  });
});

describe('nextRetryDelayMs', () => {
  it('backs off exponentially from the base delay', () => {
    expect(nextRetryDelayMs(0)).toBe(5_000);
    expect(nextRetryDelayMs(1)).toBe(10_000);
    expect(nextRetryDelayMs(2)).toBe(20_000);
    expect(nextRetryDelayMs(3)).toBe(40_000);
  });

  it('caps at five minutes so a long outage still retries', () => {
    expect(nextRetryDelayMs(20)).toBe(5 * 60_000);
    expect(nextRetryDelayMs(999)).toBe(5 * 60_000);
  });

  it('treats negative and fractional attempts as the first attempt', () => {
    expect(nextRetryDelayMs(-5)).toBe(5_000);
    expect(nextRetryDelayMs(1.9)).toBe(10_000);
  });
});

describe('shouldRetry', () => {
  it('retries only transient failures', () => {
    expect(shouldRetry('transient')).toBe(true);
    expect(shouldRetry('denied')).toBe(false);
    expect(shouldRetry('unsupported')).toBe(false);
  });
});

describe('sensorErrorMessage', () => {
  it('prefers a real message over the fallback', () => {
    expect(sensorErrorMessage(new Error('GPS busy'), 'fallback')).toBe('GPS busy');
    expect(sensorErrorMessage('string reason', 'fallback')).toBe('string reason');
    expect(sensorErrorMessage({ message: 'object reason' }, 'fallback')).toBe('object reason');
  });

  it('falls back rather than showing an empty or blank message', () => {
    expect(sensorErrorMessage(new Error('   '), 'fallback')).toBe('fallback');
    expect(sensorErrorMessage(undefined, 'fallback')).toBe('fallback');
    expect(sensorErrorMessage({}, 'fallback')).toBe('fallback');
    expect(sensorErrorMessage({ message: 5 }, 'fallback')).toBe('fallback');
  });
});