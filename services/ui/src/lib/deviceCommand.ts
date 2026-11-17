import { isAxiosError } from 'axios';
import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { api } from '../services/api';
import type { HubEntity } from './dashboards';
import { expectedState, withChange } from './deviceTiles';

/**
 * Sending a command to a device from the Devices page: the expected state is drawn at once, the
 * hub's answer decides. A refusal (a FAILURE body, which arrives with HTTP 200) or an error
 * (a 500 from execution) rolls the drawing back and shows the server's own words; nothing ever
 * says "done" for a command the hub did not accept.
 */

/** The server's words for a failed command, else a plain fallback. */
export function commandFailure(err: unknown, name: string): string {
  if (isAxiosError(err)) {
    const data = err.response?.data as { detail?: unknown; message?: unknown } | undefined;
    const said = typeof data?.message === 'string' && data.message ? data.message
      : typeof data?.detail === 'string' && data.detail ? data.detail : '';
    if (said) return said;
    if (!err.response) return `${name} could not be reached: the connection to the hub failed.`;
    return `${name} did not respond (error ${err.response.status}).`;
  }
  if (err instanceof Error && err.message) return err.message;
  return `${name} did not respond.`;
}

export interface CommandDeps {
  send: () => Promise<unknown>;
  /** Draw (or with null, clear) the expected state. */
  draw: (change: Partial<HubEntity> | null) => void;
  fail: (message: string) => void;
  done: () => void;
}

/** Draw, send, and on failure roll back and say why. Resolves true only when the hub accepted it. */
export async function runWithRollback(change: Partial<HubEntity> | null, name: string, deps: CommandDeps): Promise<boolean> {
  if (change) deps.draw(change);
  try {
    await deps.send();
    deps.done();
    return true;
  } catch (err) {
    deps.draw(null);
    deps.fail(commandFailure(err, name));
    deps.done();
    return false;
  }
}

/** Query keys whose data shows device state: refreshed after every command. */
export const STATE_KEYS = [['dashboard', 'rooms'], ['homehub-hardware']];

/**
 * One entity's controls on this page: `shown` is the entity with any pending change drawn on it,
 * `send` runs a service. A change lasts until the hub's next answer for the entity arrives.
 */
export function useEntityCommand(entity: HubEntity | null, name: string) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<{ base: HubEntity; change: Partial<HubEntity> } | null>(null);
  const [busy, setBusy] = useState(false);
  const shown = entity && pending && pending.base === entity ? withChange(entity, pending.change) : entity;

  const send = useCallback(async (service: string, data?: Record<string, unknown>, domain?: string) => {
    const e = entity;
    if (!e) return false;
    setBusy(true);
    const ok = await runWithRollback(expectedState(e, service, data), name, {
      send: () => api.callHaService(domain ?? e.domain, service, e.entity_id, data ?? null),
      draw: (change) => setPending(change ? { base: e, change } : null),
      fail: (message) => { toast.error(message, { id: `device-command-${e.entity_id}` }); },
      done: () => {
        setBusy(false);
        // The hub settles a moment after it answers (a bulb fades); read it again then.
        window.setTimeout(() => { for (const key of STATE_KEYS) void qc.invalidateQueries({ queryKey: key }); }, 400);
      },
    });
    return ok;
  }, [entity, name, qc]);

  return { shown, send, busy };
}

/**
 * A command to another entity of the device (a TV's remote, a camera's siren): no drawing ahead,
 * the same refusal handling. Resolves true only when the hub accepted it.
 */
export function useServiceCall() {
  const qc = useQueryClient();
  return useCallback(async (domain: string, service: string, entityId: string, data?: Record<string, unknown>,
    name = 'The device') => runWithRollback(null, name, {
    send: () => api.callHaService(domain, service, entityId, data ?? null),
    draw: () => undefined,
    fail: (message) => { toast.error(message, { id: `device-command-${entityId}` }); },
    done: () => {
      window.setTimeout(() => { for (const key of STATE_KEYS) void qc.invalidateQueries({ queryKey: key }); }, 400);
    },
  }), [qc]);
}
