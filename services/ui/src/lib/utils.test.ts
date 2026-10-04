import { describe, it, expect } from 'vitest';
import { formatClock } from './utils';

describe('formatClock', () => {
  it('keeps minutes-only clocks under an hour', () => {
    expect(formatClock(0)).toBe('0:00');
    expect(formatClock(7)).toBe('0:07');
    expect(formatClock(65)).toBe('1:05');
    expect(formatClock(599)).toBe('9:59');
    expect(formatClock(3599)).toBe('59:59');
  });

  it('grows an hours field past an hour instead of counting into minutes', () => {
    // 90 minutes was rendered as "90:00" by the per-component copies this replaced.
    expect(formatClock(3600)).toBe('1:00:00');
    expect(formatClock(4000)).toBe('1:06:40');
    expect(formatClock(5430)).toBe('1:30:30');
    expect(formatClock(36000)).toBe('10:00:00');
  });

  it('treats missing, negative and unparseable values as no elapsed time', () => {
    expect(formatClock(undefined)).toBe('0:00');
    expect(formatClock(null)).toBe('0:00');
    expect(formatClock(NaN)).toBe('0:00');
    expect(formatClock(-90)).toBe('0:00');
    expect(formatClock('not a number')).toBe('0:00');
  });

  it('accepts the numeric strings some player payloads send', () => {
    expect(formatClock('125')).toBe('2:05');
    expect(formatClock('3661')).toBe('1:01:01');
  });

  it('floors partial seconds rather than rounding into the next second', () => {
    expect(formatClock(59.9)).toBe('0:59');
    expect(formatClock(3599.5)).toBe('59:59');
  });
});