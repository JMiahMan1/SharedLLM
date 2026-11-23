/**
 * Echo / hassmic fleet discovery from Home Hub entity states.
 *
 * The hub publishes one `sensor.<node>_drop_in` and/or `assist_satellite.<node>`
 * per adopted Echo (project `donut` for a Dot, `crown` for a Show, running
 * `echo-dot-assist`/hassmic). Everything else is named `<node>_<purpose>`:
 * notably `sensor.<node>_drop_in_with`, `button.<node>_end_drop_in` and a
 * microphone switch whose `off` state means muted.
 * This module is deliberately pure: it groups states, never fetches them and
 * never invents values. A missing control stays `null` so callers can say so.
 */

export interface EchoState {
  entity_id: string;
  state: string;
  /** Home Hub's `GET /api/entities` puts the label here, not in attributes. */
  friendly_name?: string;
  attributes?: Record<string, unknown>;
}

export type DropInState = 'idle' | 'calling' | 'ringing' | 'connected' | 'unknown';

export interface EchoDeviceSummary {
  /** Home Hub node slug, e.g. `crown` from `sensor.crown_drop_in`. */
  node: string;
  name: string;
  satelliteEntityId: string | null;
  dropInEntityId: string | null;
  /** hassmic's `button.<node>_end_drop_in`, pressed to end an active Drop-In. */
  endDropInEntityId: string | null;
  dropInState: DropInState;
  dropInWith: string | null;
  dndEntityId: string | null;
  dndOn: boolean;
  muteEntityId: string | null;
  muted: boolean;
  wakeWordEntityId: string | null;
  wakeWord: string | null;
  wakeWordOptions: string[];
  lightRingEntityId: string | null;
  /** hassmic's own web UI (usually on :28931); only safe http(s) URLs survive. */
  webUiUrl: string | null;
  /** The Sendspin address hassmic publishes for Music Assistant pairing. */
  sendspinAddress: string | null;
  updateEntityId: string | null;
  updateAvailable: boolean;
  installedVersion: string | null;
  latestVersion: string | null;
  lastHeard: string | null;
  lastReply: string | null;
  whisperEnabled: boolean;
  /** False when the drop-in or satellite entity says the node is unavailable. */
  online: boolean;
}

const DROP_IN_STATES: readonly DropInState[] = ['idle', 'calling', 'ringing', 'connected', 'unknown'];

/** Suffixes are longest first: `_do_not_disturb` must win over `_dnd`. */
const PURPOSE_SUFFIXES: Record<string, readonly string[]> = {
  dnd: ['_do_not_disturb', '_dnd_enabled', '_dnd'],
  mute: ['_microphone_mute', '_muted', '_mute'],
  wakeWord: ['_wake_word', '_wakeword'],
  lightRing: ['_light_ring', '_led_ring', '_lights', '_ring'],
  webUi: ['_web_ui_address', '_webui_address'],
  sendspin: ['_sendspin_address', '_sendspin'],
  update: ['_firmware', '_update'],
  lastHeard: ['_last_heard'],
  lastReply: ['_last_reply'],
};

const PURPOSE_DOMAINS: Record<string, readonly string[]> = {
  dnd: ['input_boolean', 'switch', 'binary_sensor'],
  mute: ['input_boolean', 'switch', 'binary_sensor'],
  wakeWord: ['select', 'input_select'],
  lightRing: ['light'],
  webUi: ['sensor'],
  sendspin: ['sensor'],
  update: ['update'],
  lastHeard: ['sensor'],
  lastReply: ['sensor'],
};

interface Group {
  dropIn?: EchoState;
  satellite?: EchoState;
  dropInWith?: EchoState;
  endDropIn?: EchoState;
  dnd?: EchoState;
  mute?: EchoState;
  microphone?: EchoState;
  wakeWord?: EchoState;
  lightRing?: EchoState;
  webUi?: EchoState;
  sendspin?: EchoState;
  update?: EchoState;
  lastHeard?: EchoState;
  lastReply?: EchoState;
  whisper?: EchoState;
}

function attrs(state: EchoState | undefined): Record<string, unknown> {
  return state?.attributes ?? {};
}

function text(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function on(value: unknown): boolean {
  return value === true || value === 'on' || value === 'true' || value === 1;
}

function domainOf(entityId: string): string {
  return entityId.split('.', 1)[0] ?? '';
}

function objectIdOf(entityId: string): string {
  const dot = entityId.indexOf('.');
  return dot < 0 ? entityId : entityId.slice(dot + 1);
}

/** Only absolute http(s) URLs are safe to put in `href`/`src`; never `javascript:`. */
export function isSafeHttpUrl(value: unknown): value is string {
  const raw = text(value);
  if (!raw || /[\u0000-\u001f]/.test(raw)) return false;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return false;
  }
  return (parsed.protocol === 'http:' || parsed.protocol === 'https:')
    && Boolean(parsed.hostname)
    && !parsed.username
    && !parsed.password;
}

/** hassmic reports `host:port` for Sendspin, not a URL; reject anything script-like. */
function safeSendspinAddress(value: unknown): string | null {
  const raw = text(value);
  if (!raw) return null;
  if (raw.includes('://')) return isSafeHttpUrl(raw) ? raw : null;
  return /^[A-Za-z0-9._-]+:\d{1,5}$/.test(raw) ? raw : null;
}

function normaliseDropInState(value: unknown): DropInState {
  const raw = (text(value) ?? '').toLowerCase();
  if (DROP_IN_STATES.includes(raw as DropInState)) return raw as DropInState;
  if (raw === 'on' || raw === 'detected' || raw === 'active' || raw === 'in_call') return 'connected';
  if (raw === 'off' || raw === 'clear' || raw === 'none') return 'idle';
  if (raw === 'ring') return 'ringing';
  if (raw === 'call') return 'calling';
  return 'unknown';
}

function usefulState(state: EchoState | undefined): string | null {
  const value = text(state?.state);
  if (!value || value === 'unknown' || value === 'unavailable' || value === 'none') return null;
  return value;
}

function prettyName(node: string): string {
  return node
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function dropInName(raw: string): string {
  return raw.replace(/\s*[-–—]?\s*drop[\s-]?in\s*$/i, '').trim();
}

/** The one entity that proves a state belongs to an adopted Echo. */
function anchorNode(state: EchoState): { node: string; kind: 'dropIn' | 'satellite' } | null {
  const objectId = objectIdOf(state.entity_id);
  const domain = domainOf(state.entity_id);
  // Only a sensor is hassmic's Drop-In anchor. An unrelated
  // `button.kitchen_end_drop_in` must not invent a ghost node `kitchen_end`.
  if (domain === 'sensor') {
    const dropIn = /^(.+)_drop_in$/.exec(objectId);
    if (dropIn) return { node: dropIn[1], kind: 'dropIn' };
  }
  if (domain === 'assist_satellite') {
    return { node: objectId.replace(/_assist_satellite$/, ''), kind: 'satellite' };
  }
  return null;
}

function assign(group: Group, purpose: string, state: EchoState): void {
  switch (purpose) {
    case 'dnd': group.dnd ??= state; break;
    case 'mute': group.mute ??= state; break;
    case 'wakeWord': group.wakeWord ??= state; break;
    case 'lightRing': group.lightRing ??= state; break;
    case 'webUi': group.webUi ??= state; break;
    case 'sendspin': group.sendspin ??= state; break;
    case 'update': group.update ??= state; break;
    case 'lastHeard': group.lastHeard ??= state; break;
    case 'lastReply': group.lastReply ??= state; break;
    default: break;
  }
}

/**
 * Group a Home Hub entity snapshot into one row per adopted Echo.
 * States without an anchor (a `sensor.*_drop_in` or `assist_satellite.*`) are
 * ignored; a node with no known service is still returned with null controls.
 */
export function extractEchoFleet(states: EchoState[]): EchoDeviceSummary[] {
  const groups = new Map<string, Group>();

  for (const state of states) {
    const anchor = anchorNode(state);
    if (!anchor) continue;
    const group = groups.get(anchor.node) ?? {};
    if (anchor.kind === 'dropIn') group.dropIn ??= state;
    else group.satellite ??= state;
    groups.set(anchor.node, group);
  }

  for (const state of states) {
    const domain = domainOf(state.entity_id);
    const objectId = objectIdOf(state.entity_id);
    // Real hassmic publishes these as their own entities, not just attributes
    // on `sensor.<node>_drop_in`. Only attach them to an anchored node.
    if (domain === 'sensor' && objectId.endsWith('_drop_in_with')) {
      const node = objectId.slice(0, -'_drop_in_with'.length);
      const group = groups.get(node);
      if (group) group.dropInWith ??= state;
      continue;
    }
    if (domain === 'button' && objectId.endsWith('_end_drop_in')) {
      const node = objectId.slice(0, -'_end_drop_in'.length);
      const group = groups.get(node);
      if (group) group.endDropIn ??= state;
      continue;
    }
    if ((domain === 'switch' || domain === 'binary_sensor') && objectId.endsWith('_microphone')) {
      const node = objectId.slice(0, -'_microphone'.length);
      const group = groups.get(node);
      if (group) group.microphone ??= state;
      continue;
    }
    if (domain === 'binary_sensor' && objectId.endsWith('_last_request_whispered')) {
      const node = objectId.slice(0, -'_last_request_whispered'.length);
      const group = groups.get(node);
      if (group) group.whisper ??= state;
      continue;
    }
    for (const [purpose, suffixes] of Object.entries(PURPOSE_SUFFIXES)) {
      if (!PURPOSE_DOMAINS[purpose]?.includes(domain)) continue;
      const suffix = suffixes.find((candidate) => objectId.endsWith(candidate));
      if (!suffix) continue;
      const node = objectId.slice(0, -suffix.length);
      const group = groups.get(node);
      if (group) assign(group, purpose, state);
      break;
    }
  }

  return [...groups.entries()]
    .map(([node, group]): EchoDeviceSummary => {
      const dropInAttrs = attrs(group.dropIn);
      const satelliteAttrs = attrs(group.satellite);
      const named = (text(group.satellite?.friendly_name)
        ?? text(satelliteAttrs.friendly_name)
        ?? dropInName(text(group.dropIn?.friendly_name) ?? text(dropInAttrs.friendly_name) ?? '')
        ?? '') || prettyName(node);
      const wakeWord = usefulState(group.wakeWord);
      const rawOptions = attrs(group.wakeWord).options;
      const options = Array.isArray(rawOptions)
        ? rawOptions.filter((option): option is string => typeof option === 'string' && option.trim() !== '')
        : [];
      const webUiCandidate = usefulState(group.webUi)
        ?? text(attrs(group.webUi).url)
        ?? text(attrs(group.webUi).address);
      const online = [group.satellite, group.dropIn]
        .filter((state): state is EchoState => Boolean(state))
        .every((state) => state.state !== 'unavailable' && state.state !== 'unknown');

      return {
        node,
        name: dropInName(named) || prettyName(node),
        satelliteEntityId: group.satellite?.entity_id ?? null,
        dropInEntityId: group.dropIn?.entity_id ?? null,
        endDropInEntityId: group.endDropIn?.entity_id ?? null,
        dropInState: normaliseDropInState(group.dropIn?.state ?? dropInAttrs.state),
        dropInWith: usefulState(group.dropInWith)
          ?? text(dropInAttrs.drop_in_with)
          ?? text(dropInAttrs.with)
          ?? text(dropInAttrs.caller)
          ?? text(dropInAttrs.peer),
        dndEntityId: group.dnd?.entity_id ?? null,
        dndOn: on(group.dnd?.state),
        muteEntityId: group.mute?.entity_id ?? group.microphone?.entity_id ?? null,
        // hassmic uses `*_microphone` (off = muted) as well as `*_mute` (on = muted).
        muted: on(group.mute?.state) || group.microphone?.state === 'off',
        wakeWordEntityId: group.wakeWord?.entity_id ?? null,
        wakeWord,
        wakeWordOptions: wakeWord && !options.includes(wakeWord) ? [wakeWord, ...options] : options,
        lightRingEntityId: group.lightRing?.entity_id ?? null,
        webUiUrl: isSafeHttpUrl(webUiCandidate) ? webUiCandidate : null,
        sendspinAddress: safeSendspinAddress(
          usefulState(group.sendspin) ?? text(attrs(group.sendspin).address),
        ),
        updateEntityId: group.update?.entity_id ?? null,
        updateAvailable: on(group.update?.state),
        installedVersion: text(attrs(group.update).installed_version),
        latestVersion: text(attrs(group.update).latest_version),
        lastHeard: usefulState(group.lastHeard),
        lastReply: usefulState(group.lastReply),
        whisperEnabled: Boolean(group.whisper),
        online,
      };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}
