import { describe, expect, it } from 'vitest';
import { formatState, unavailableLabel } from './dashboards';

describe('unavailableLabel', () => {
  it('says a light that alone stopped answering has no power', () => {
    expect(unavailableLabel({ domain: 'light', offline: { cause: 'device', integration: 'Matter' } }))
      .toBe('No power (wall switch off?)');
  });

  it('names the integration when all of it is out, not each bulb', () => {
    expect(unavailableLabel({ domain: 'light', offline: { cause: 'integration', integration: 'Matter' } }))
      .toBe('Matter not connected');
  });

  it('keeps other devices plain and older hubs unchanged', () => {
    expect(unavailableLabel({ domain: 'sensor', offline: { cause: 'device', integration: 'WiZ' } })).toBe('Not answering');
    expect(unavailableLabel({ domain: 'light' })).toBe('Unavailable');
    expect(formatState({ state: 'unavailable', unit: null, domain: 'sensor', offline: null })).toBe('Unavailable');
  });
});
