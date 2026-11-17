import { AxiosError, AxiosHeaders } from 'axios';
import { describe, expect, it, vi } from 'vitest';
import { commandFailure, runWithRollback } from './deviceCommand';

const axios500 = (data: unknown) => new AxiosError('boom', '500', undefined, undefined,
  { status: 500, statusText: 'x', data, headers: {}, config: { headers: new AxiosHeaders() } });

describe('a command with rollback', () => {
  it('draws first, keeps it on success, and never reports a failure', async () => {
    const deps = { send: vi.fn().mockResolvedValue({ status: 'SUCCESS' }), draw: vi.fn(), fail: vi.fn(), done: vi.fn() };
    expect(await runWithRollback({ state: 'on' }, 'Lamp', deps)).toBe(true);
    expect(deps.draw).toHaveBeenCalledTimes(1);
    expect(deps.draw).toHaveBeenCalledWith({ state: 'on' });
    expect(deps.fail).not.toHaveBeenCalled();
    expect(deps.done).toHaveBeenCalled();
  });
  it('rolls back and says the hub\'s words when refused', async () => {
    const deps = { send: vi.fn().mockRejectedValue(new Error('Lamp did not answer')), draw: vi.fn(), fail: vi.fn(), done: vi.fn() };
    expect(await runWithRollback({ state: 'on' }, 'Lamp', deps)).toBe(false);
    expect(deps.draw).toHaveBeenLastCalledWith(null);
    expect(deps.fail).toHaveBeenCalledWith('Lamp did not answer');
  });
  it('words a server error from its body, else plainly', () => {
    expect(commandFailure(axios500({ detail: 'Execution failed: hub refused' }), 'Lamp')).toBe('Execution failed: hub refused');
    expect(commandFailure(axios500({}), 'Lamp')).toBe('Lamp did not respond (error 500).');
    expect(commandFailure(new AxiosError('Network Error'), 'Lamp')).toMatch(/could not be reached/);
    expect(commandFailure(null, 'Lamp')).toBe('Lamp did not respond.');
  });
});
