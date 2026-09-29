import { describe, it, expect } from 'vitest';
import { rethrowWithServerDetail } from '../services/api';

/**
 * The response interceptor rejects the raw axios error, whose message is only
 * "Request failed with status code 400" — the service's own `detail` is lost.
 * Anything an operator has to act on goes through this helper instead, so a
 * refusal is never flattened into a status code they cannot do anything about.
 */
const fail = (response?: { data?: unknown }) => {
  try {
    rethrowWithServerDetail({ response }, 'Fallback message');
  } catch (error) {
    return error as Error;
  }
  throw new Error('rethrowWithServerDetail did not throw');
};

describe('rethrowWithServerDetail', () => {
  it('surfaces a string detail verbatim', () => {
    const error = fail({ data: { detail: 'Unknown username(s) in the permit list: alise.' } });
    expect(error.message).toBe('Unknown username(s) in the permit list: alise.');
  });

  it('unwraps a FastAPI validation error list', () => {
    const error = fail({ data: { detail: [{ msg: 'field required', loc: ['body', 'protected'] }] } });
    expect(error.message).toBe('field required');
  });

  it('falls back when there is no response at all (a network failure)', () => {
    expect(fail(undefined).message).toBe('Fallback message');
  });

  it('falls back when the detail is not a string, rather than printing [object Object]', () => {
    expect(fail({ data: { detail: 42 } }).message).toBe('Fallback message');
    expect(fail({ data: {} }).message).toBe('Fallback message');
  });

  it('treats a blank detail as no detail', () => {
    expect(fail({ data: { detail: '   ' } }).message).toBe('Fallback message');
  });

  it('survives a null or undefined error', () => {
    expect(fail(undefined).message).toBe('Fallback message');
    expect(fail({ data: { detail: 'real reason' } }).message).toBe('real reason');
  });
});
