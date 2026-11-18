/** The Identity setting that turns the HomeHub integration on (blank: off). */
export const HOMEHUB_SETTING = 'homehub_url';

/** Said once the address is saved: every service reads it once, at startup. */
export const HOMEHUB_RESTART_NOTE = 'Services read this address when they start, so the change applies after the next restart.';

/** What the HomeHub card says about HomeHub right now (the running services' view). */
export function homehubStatusLine(running: boolean, saved: string | null): string {
  if (running) return saved ? `Connected: ${saved.replace(/^https?:\/\//, '')}` : 'Connected';
  if (saved) return 'Saved; applies after a restart';
  return 'Off: no address set';
}

/** Said where a page needs a home hub and none is connected. */
export const CONNECT_HUB_TITLE = 'Connect a home hub in Settings';

/**
 * Home Assistant is an optional integration (HomeHub supersedes it here). While
 * it is off no Home Assistant login is used for anything, though a saved one is kept.
 */
export const HA_OFF_NOTICE = 'The Home Assistant integration is off, so no Home Assistant login is used. '
  + 'A saved login is kept. An admin turns it on by setting the household Home Assistant address (ha_url) in Admin settings.';
