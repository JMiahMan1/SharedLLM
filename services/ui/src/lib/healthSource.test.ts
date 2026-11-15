import { describe, expect, it } from 'vitest';
import { healthSourceName } from './healthSource';

describe('healthSourceName', () => {
  it('names the sources a person meets', () => {
    expect(healthSourceName('health_connect')).toBe('Health Connect');
    expect(healthSourceName('manual')).toBe('hand logged');
    expect(healthSourceName('usage_estimate')).toBe('estimated from device use');
  });

  it('never shows an underscore id', () => {
    expect(healthSourceName('some_new_source')).toBe('some new source');
    expect(healthSourceName(null)).toBe('');
  });
});
