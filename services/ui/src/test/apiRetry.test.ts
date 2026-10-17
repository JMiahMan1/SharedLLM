import { describe, expect, it } from 'vitest';
import { isRetryableRequest, waitForImageEdit } from '../services/api';

/**
 * The public nginx proxy gives a request 60 seconds before it answers 504.
 * Replaying a POST there used to start a second copy of the same work, so one
 * "Make it way better" tap produced a fresh image edit every minute.
 */
describe('isRetryableRequest', () => {
  it('never replays a POST, even when the proxy times out', () => {
    expect(isRetryableRequest('POST', 504, undefined)).toBe(false);
    expect(isRetryableRequest('post', undefined, 'ECONNABORTED')).toBe(false);
  });

  it('does not replay other methods with side effects', () => {
    expect(isRetryableRequest('PATCH', 503, undefined)).toBe(false);
    expect(isRetryableRequest('PUT', 408, undefined)).toBe(false);
    expect(isRetryableRequest('DELETE', undefined, 'ERR_NETWORK')).toBe(false);
  });

  it('retries idempotent requests on a proxy timeout', () => {
    expect(isRetryableRequest('GET', 504, undefined)).toBe(true);
    expect(isRetryableRequest('HEAD', 503, undefined)).toBe(true);
    expect(isRetryableRequest('OPTIONS', 408, undefined)).toBe(true);
    expect(isRetryableRequest('GET', undefined, 'ECONNABORTED')).toBe(true);
    expect(isRetryableRequest('GET', undefined, 'ERR_NETWORK')).toBe(true);
  });

  it('treats a missing method as GET', () => {
    expect(isRetryableRequest(undefined, 504, undefined)).toBe(true);
  });

  it('leaves ordinary server and application errors alone', () => {
    expect(isRetryableRequest('GET', 500, undefined)).toBe(false);
    expect(isRetryableRequest('GET', 400, undefined)).toBe(false);
    expect(isRetryableRequest('GET', undefined, 'ERR_BAD_REQUEST')).toBe(false);
    expect(isRetryableRequest('GET', undefined, undefined)).toBe(false);
  });
});

describe('waitForImageEdit', () => {
  const sleep = () => Promise.resolve();

  it('keeps polling until the edit succeeds', async () => {
    const jobs = [
      { status: 'PENDING' },
      { status: 'RUNNING' },
      { status: 'SUCCESS', data: [{ b64_json: 'AAAA' }] },
    ];
    const fetchJob = async () => jobs.shift()!;

    const result = await waitForImageEdit(fetchJob, 'job-1', { sleep, timeoutMs: 60_000 });

    expect(result).toEqual({ status: 'SUCCESS', data: [{ b64_json: 'AAAA' }] });
    expect(jobs).toHaveLength(0);
  });

  it('returns a backend refusal with its reason', async () => {
    const fetchJob = async () => ({ status: 'FAILURE', message: 'no image model configured' });

    const result = await waitForImageEdit(fetchJob, 'job-2', { sleep });

    expect(result.status).toBe('FAILURE');
    expect(result.message).toContain('no image model configured');
  });

  it('reports a job lost to a gateway restart instead of polling forever', async () => {
    const fetchJob = async () => {
      throw Object.assign(new Error('Not Found'), { response: { status: 404 } });
    };

    const result = await waitForImageEdit(fetchJob, 'job-3', { sleep });

    expect(result.status).toBe('FAILURE');
    expect(result.message).toContain('server restart');
  });

  it('stops when the request is aborted', async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(
      waitForImageEdit(async () => ({ status: 'PENDING' }), 'job-4', {
        sleep,
        signal: controller.signal,
      }),
    ).rejects.toThrow('cancelled');
  });

  it('gives up after its overall deadline', async () => {
    let calls = 0;
    const fetchJob = async () => {
      calls += 1;
      return { status: 'PENDING' };
    };

    const result = await waitForImageEdit(fetchJob, 'job-5', { sleep, timeoutMs: 0 });

    expect(result.status).toBe('FAILURE');
    expect(result.message).toContain('did not finish in time');
    expect(calls).toBe(0);
  });
});
