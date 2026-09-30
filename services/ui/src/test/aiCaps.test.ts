import { describe, it, expect } from 'vitest';
import { statusOf, capabilityLabel, summarise, toneFor } from '../lib/aiCaps';
import type { AiCapability } from '../types/api';

const cap = (available: boolean | null, key = 'k', label = 'Label'): AiCapability => ({
  key,
  label,
  available,
  detail: 'd',
});

describe('statusOf', () => {
  it('maps the three real states', () => {
    expect(statusOf(cap(true))).toBe('ok');
    expect(statusOf(cap(false))).toBe('no');
    expect(statusOf(cap(null))).toBe('unknown');
  });

  it('does not treat unconfirmed as available', () => {
    // The whole point: an unverified capability must never read as working.
    expect(statusOf(cap(null))).not.toBe('ok');
  });
});

describe('capabilityLabel', () => {
  it('names the state in words', () => {
    expect(capabilityLabel(cap(true, 'a', 'Edit'))).toBe('Edit — ready');
    expect(capabilityLabel(cap(false, 'a', 'Edit'))).toBe('Edit — unavailable');
    expect(capabilityLabel(cap(null, 'a', 'Edit'))).toBe('Edit — unconfirmed');
  });
});

describe('summarise', () => {
  it('counts each state separately', () => {
    const s = summarise([cap(true, 'a'), cap(true, 'b'), cap(false, 'c'), cap(null, 'd')]);
    expect(s).toEqual({ total: 4, ready: 2, unavailable: 1, unconfirmed: 1 });
  });

  it('handles an empty list', () => {
    expect(summarise([])).toEqual({ total: 0, ready: 0, unavailable: 0, unconfirmed: 0 });
  });
});

describe('toneFor', () => {
  it('warns when something is genuinely broken', () => {
    expect(toneFor({ total: 2, ready: 1, unavailable: 1, unconfirmed: 0 })).toBe('warn');
  });

  it('stays neutral when something is merely unconfirmed', () => {
    expect(toneFor({ total: 2, ready: 1, unavailable: 0, unconfirmed: 1 })).toBe('neutral');
  });

  it('is calm only when everything is confirmed working', () => {
    expect(toneFor({ total: 2, ready: 2, unavailable: 0, unconfirmed: 0 })).toBe('ok');
  });
});
