import { apiClient } from '../services/api';

/**
 * "Jarvis is updating": scripts/deploy_remote.sh writes data/deploy_status/status.json on the
 * server while it restarts services, and Caddy serves it at /api/deploy-status (from a file,
 * so it answers while the services are down). `at` is when it was written, in epoch seconds;
 * `app` says whether the web app itself is part of the deploy.
 */
export interface DeployStatus {
  updating: boolean;
  at: number;
  app: boolean;
}

/** A flag this old is a deploy that died before it could clear it: ignore it. */
export const STALE_AFTER_S = 15 * 60;
export const POLL_IDLE_MS = 30_000;
export const POLL_UPDATING_MS = 3_000;

/** The deploy flag, or null when there is none or it cannot be read (never an error to show). */
export async function getDeployStatus(): Promise<DeployStatus | null> {
  try {
    const resp = await apiClient.get('/api/deploy-status', { silent: true, timeout: 5_000 });
    const d = resp.data as Partial<DeployStatus> | null;
    if (!d || typeof d.updating !== 'boolean' || typeof d.at !== 'number') return null;
    return { updating: d.updating, at: d.at, app: d.app === true };
  } catch {
    return null;
  }
}

export type DeployBannerPhase = 'none' | 'updating' | 'updated';

/**
 * What the banner shows next. "updating" while a fresh flag is up; once it comes down, a browser
 * that saw it and whose app was part of the deploy is offered a reload ("updated") until it
 * dismisses it. The phone app updates itself (appUpdater), so it is never asked to reload.
 */
export function nextPhase(prev: DeployBannerPhase, status: DeployStatus | null, nowMs: number, native: boolean): DeployBannerPhase {
  if (status?.updating && nowMs / 1000 - status.at < STALE_AFTER_S) return 'updating';
  if (prev === 'updating' && status && !status.updating && status.app && !native) return 'updated';
  if (prev === 'updated') return 'updated';
  return 'none';
}
