import type { HubEntity } from './dashboards';

/**
 * The device control library's decisions (components/devices/controls): which control an entity
 * gets, and what that control offers, from Home Assistant's own contract only (the entity's
 * domain, device_class, supported_features bits and attributes). Nothing here knows a brand or a
 * device, so a new device of a known kind needs no new code. Pure; unit-tested in
 * deviceControls.test.ts.
 */

export type ControlKind = 'light' | 'switch' | 'fan' | 'tv' | 'speaker' | 'remote' | 'vacuum' | 'camera' | 'climate'
  | 'cover' | 'garage' | 'lock' | 'siren' | 'button' | 'number' | 'select' | 'time' | 'update' | 'sensor' | 'binary'
  | 'image' | 'scene' | 'other';

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v
  : typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v)) ? Number(v) : null);
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String).filter(Boolean) : []);
const features = (e: Pick<HubEntity, 'attributes'>) => num(e.attributes?.supported_features) ?? 0;
const has = (e: Pick<HubEntity, 'attributes'>, bit: number) => (features(e) & bit) !== 0;

/** The words that say a media player is a TV when its device_class does not. */
const TV_WORDS = /\b(tv|television|roku|chromecast|apple tv|shield|fire ?tv|projector|bravia|webos|tizen)\b/i;

/** Which control an entity gets. */
export function controlKind(e: Pick<HubEntity, 'domain' | 'device_class' | 'attributes' | 'name'>): ControlKind {
  switch (e.domain) {
    case 'light': return 'light';
    case 'switch': case 'input_boolean': case 'humidifier': return 'switch';
    case 'fan': return 'fan';
    case 'media_player': {
      if (e.device_class === 'tv') return 'tv';
      if (e.device_class === 'speaker' || e.device_class === 'receiver') return 'speaker';
      return TV_WORDS.test(e.name || '') ? 'tv' : 'speaker';
    }
    case 'remote': return 'remote';
    case 'vacuum': case 'lawn_mower': return 'vacuum';
    case 'camera': return 'camera';
    case 'climate': case 'water_heater': return 'climate';
    case 'cover': case 'valve':
      return e.device_class === 'garage' || e.device_class === 'gate' ? 'garage' : 'cover';
    case 'lock': return 'lock';
    case 'siren': return 'siren';
    case 'button': case 'input_button': return 'button';
    case 'number': case 'input_number': return 'number';
    case 'select': case 'input_select': return 'select';
    case 'time': return 'time';
    case 'update': return 'update';
    case 'sensor': return 'sensor';
    case 'binary_sensor': return 'binary';
    case 'image': return 'image';
    case 'scene': case 'script': return 'scene';
    default: return 'other';
  }
}

// -- light (LightEntityFeature: EFFECT 4, FLASH 8, TRANSITION 32) ----------------------------------

export function lightEffects(e: Pick<HubEntity, 'attributes'>): string[] {
  const effects = list(e.attributes?.effect_list);
  return effects.length && (has(e, 4) || !num(e.attributes?.supported_features)) ? effects : [];
}

// -- media (MediaPlayerEntityFeature) ---------------------------------------------------------------

export const MP = {
  PAUSE: 1, SEEK: 2, VOLUME_SET: 4, VOLUME_MUTE: 8, PREVIOUS: 16, NEXT: 32, TURN_ON: 128, TURN_OFF: 256,
  PLAY_MEDIA: 512, VOLUME_STEP: 1024, SELECT_SOURCE: 2048, STOP: 4096, PLAY: 16384, SHUFFLE: 32768,
  SELECT_SOUND_MODE: 65536, REPEAT: 262144, GROUPING: 524288,
} as const;

export interface MediaFeatures {
  power: boolean; play: boolean; pause: boolean; stop: boolean; previous: boolean; next: boolean; seek: boolean;
  volume: boolean; volumeStep: boolean; mute: boolean; sources: string[]; soundModes: string[]; grouping: boolean;
}

export function mediaFeatures(e: Pick<HubEntity, 'attributes'>): MediaFeatures {
  const a = e.attributes ?? {};
  const f = features(e);
  const bit = (b: number) => (f & b) !== 0;
  return {
    power: bit(MP.TURN_ON) || bit(MP.TURN_OFF),
    play: bit(MP.PLAY) || bit(MP.PAUSE),
    pause: bit(MP.PAUSE),
    stop: bit(MP.STOP),
    previous: bit(MP.PREVIOUS),
    next: bit(MP.NEXT),
    seek: bit(MP.SEEK) && num(a.media_duration) != null,
    volume: bit(MP.VOLUME_SET),
    volumeStep: bit(MP.VOLUME_STEP) || bit(MP.VOLUME_SET),
    mute: bit(MP.VOLUME_MUTE),
    sources: bit(MP.SELECT_SOURCE) ? list(a.source_list) : [],
    soundModes: bit(MP.SELECT_SOUND_MODE) ? list(a.sound_mode_list) : [],
    grouping: bit(MP.GROUPING),
  };
}

/** "3:05" / "1:02:03" for a media position in seconds. */
export function clock(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '';
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

/** The position now: HA reports it as of `media_position_updated_at` and it runs on while playing. */
export function mediaPosition(e: Pick<HubEntity, 'attributes' | 'state'>, now = Date.now()): number | null {
  const a = e.attributes ?? {};
  const pos = num(a.media_position);
  if (pos == null) return null;
  const at = typeof a.media_position_updated_at === 'string' ? new Date(a.media_position_updated_at).getTime() : NaN;
  const ran = e.state === 'playing' && Number.isFinite(at) ? Math.max(0, (now - at) / 1000) : 0;
  const dur = num(a.media_duration);
  return dur != null ? Math.min(dur, pos + ran) : pos + ran;
}

/**
 * The TV remote's keys, each with the command names the common remote integrations use for it
 * (Android TV Remote, Roku, Apple TV, Fire TV, LG, Samsung). The first one the remote lists
 * (`command_list`) is sent; with no list, the first name is sent and the hub decides.
 */
export const TV_KEYS: Record<string, string[]> = {
  up: ['DPAD_UP', 'up', 'UP', 'KEY_UP'],
  down: ['DPAD_DOWN', 'down', 'DOWN', 'KEY_DOWN'],
  left: ['DPAD_LEFT', 'left', 'LEFT', 'KEY_LEFT'],
  right: ['DPAD_RIGHT', 'right', 'RIGHT', 'KEY_RIGHT'],
  ok: ['DPAD_CENTER', 'select', 'ENTER', 'OK', 'KEY_ENTER'],
  back: ['BACK', 'back', 'KEY_RETURN', 'menu'],
  home: ['HOME', 'home', 'KEY_HOME', 'top_menu'],
  menu: ['MENU', 'info', 'KEY_MENU', 'SETTINGS'],
  volume_up: ['VOLUME_UP', 'volume_up', 'KEY_VOLUP'],
  volume_down: ['VOLUME_DOWN', 'volume_down', 'KEY_VOLDOWN'],
  mute: ['MUTE', 'volume_mute', 'KEY_MUTE'],
  channel_up: ['CHANNEL_UP', 'channel_up', 'KEY_CHUP'],
  channel_down: ['CHANNEL_DOWN', 'channel_down', 'KEY_CHDOWN'],
};

/** The command to send for a key on this remote, or null when its list says it has none. */
export function keyCommand(remote: Pick<HubEntity, 'attributes'> | null, key: string): string | null {
  const names = TV_KEYS[key] ?? [key];
  const known = list(remote?.attributes?.command_list);
  if (!known.length) return names[0];
  return names.find((n) => known.includes(n)) ?? null;
}

/** The apps or inputs a TV can switch to: its own sources, else its remote's activities. */
export function tvApps(player: Pick<HubEntity, 'attributes'>, remote: Pick<HubEntity, 'attributes'> | null):
  { via: 'source' | 'activity'; options: string[]; current: string } {
  const sources = list(player.attributes?.source_list);
  if (sources.length) return { via: 'source', options: sources, current: String(player.attributes?.source ?? '') };
  const acts = list(remote?.attributes?.activity_list);
  return { via: 'activity', options: acts, current: String(remote?.attributes?.current_activity ?? '') };
}

// -- vacuum (VacuumEntityFeature) ---------------------------------------------------------------------

export const VAC = { PAUSE: 4, STOP: 8, RETURN_HOME: 16, FAN_SPEED: 32, BATTERY: 64, SEND_COMMAND: 256, LOCATE: 512,
  CLEAN_SPOT: 1024, MAP: 2048, STATE: 4096, START: 8192, CLEAN_AREA: 16384 } as const;

export interface VacuumFeatures {
  start: boolean; pause: boolean; stop: boolean; home: boolean; locate: boolean; spot: boolean;
  fanSpeeds: string[]; rooms: boolean;
}

export function vacuumFeatures(e: Pick<HubEntity, 'attributes' | 'abilities'>): VacuumFeatures {
  const f = features(e);
  const bit = (b: number) => (f & b) !== 0;
  const legacy = f === 0;
  return {
    start: bit(VAC.START) || legacy, pause: bit(VAC.PAUSE) || legacy, stop: bit(VAC.STOP),
    home: bit(VAC.RETURN_HOME) || legacy, locate: bit(VAC.LOCATE), spot: bit(VAC.CLEAN_SPOT),
    fanSpeeds: bit(VAC.FAN_SPEED) ? list(e.attributes?.fan_speed_list) : [],
    rooms: bit(VAC.CLEAN_AREA) || (bit(VAC.SEND_COMMAND) && Array.isArray(e.attributes?.segments))
      || Boolean(e.abilities?.includes('clean_area')),
  };
}

/** Readings worth a vacuum's panel among its device's sensors: battery, errors, consumables. */
export function vacuumReadings(siblings: HubEntity[]): { battery: HubEntity | null; errors: HubEntity[]; wear: HubEntity[] } {
  const sensors = siblings.filter((s) => s.domain === 'sensor' || s.domain === 'binary_sensor');
  const battery = sensors.find((s) => s.device_class === 'battery') ?? null;
  const errors = sensors.filter((s) => /\b(error|problem|fault)\b/i.test(s.name) || s.device_class === 'problem');
  const wear = sensors.filter((s) => /\b(filter|brush|mop|sensor dirty|consumable|dust ?bag|strainer)\b/i.test(s.name)
    && !errors.includes(s));
  return { battery, errors, wear };
}

/** The vacuum's map: an image entity of the same device whose name says map, else its only image. */
export function mapImage(siblings: HubEntity[]): HubEntity | null {
  const images = siblings.filter((s) => s.domain === 'image');
  return images.find((s) => /map|floor/i.test(`${s.name} ${s.entity_id}`)) ?? images[0] ?? null;
}

// -- climate (ClimateEntityFeature) -------------------------------------------------------------------

export const CLIMATE = { TARGET: 1, RANGE: 2, HUMIDITY: 4, FAN: 8, PRESET: 16, SWING: 32, TURN_OFF: 128, TURN_ON: 256 } as const;

export interface ClimateFeatures {
  single: boolean; range: boolean; modes: string[]; fanModes: string[]; presets: string[]; swingModes: string[];
  min: number; max: number; step: number; unit: string;
}

export function climateFeatures(e: Pick<HubEntity, 'attributes' | 'state'>): ClimateFeatures {
  const a = e.attributes ?? {};
  const f = features(e);
  const bit = (b: number) => (f & b) !== 0;
  const unit = typeof a.temperature_unit === 'string' ? a.temperature_unit
    : typeof e.attributes?.unit_of_measurement === 'string' ? String(e.attributes.unit_of_measurement) : '°';
  const fahrenheit = /F/.test(unit) || (num(a.max_temp) ?? 0) > 50;
  return {
    single: bit(CLIMATE.TARGET) || (f === 0 && num(a.temperature) != null),
    range: bit(CLIMATE.RANGE) && (num(a.target_temp_low) != null || num(a.target_temp_high) != null || !bit(CLIMATE.TARGET)),
    modes: list(a.hvac_modes),
    fanModes: bit(CLIMATE.FAN) ? list(a.fan_modes) : [],
    presets: bit(CLIMATE.PRESET) ? list(a.preset_modes) : [],
    swingModes: bit(CLIMATE.SWING) ? list(a.swing_modes) : [],
    min: num(a.min_temp) ?? (fahrenheit ? 45 : 7),
    max: num(a.max_temp) ?? (fahrenheit ? 95 : 35),
    step: num(a.target_temp_step) ?? (fahrenheit ? 1 : 0.5),
    unit: unit === '°' || unit.startsWith('°') ? unit : `°${unit}`,
  };
}

// -- cover, fan, siren, camera ---------------------------------------------------------------------------

/** CoverEntityFeature: OPEN 1, CLOSE 2, SET_POSITION 4, STOP 8. */
export function coverFeatures(e: Pick<HubEntity, 'attributes'>) {
  const f = features(e);
  const legacy = f === 0;
  return { open: (f & 1) !== 0 || legacy, close: (f & 2) !== 0 || legacy, position: (f & 4) !== 0, stop: (f & 8) !== 0 || legacy };
}

/** FanEntityFeature: SET_SPEED 1, OSCILLATE 2, DIRECTION 4, PRESET_MODE 8. */
export function fanFeatures(e: Pick<HubEntity, 'attributes'>) {
  const f = features(e);
  return { speed: (f & 1) !== 0 || num(e.attributes?.percentage) != null, oscillate: (f & 2) !== 0,
    direction: (f & 4) !== 0, presets: (f & 8) !== 0 ? list(e.attributes?.preset_modes) : [] };
}

/** SirenEntityFeature: TURN_ON 1, TURN_OFF 2, TONES 4, VOLUME_SET 8, DURATION 16. */
export function sirenFeatures(e: Pick<HubEntity, 'attributes'>) {
  const f = features(e);
  const tones = e.attributes?.available_tones;
  const toneList = Array.isArray(tones) ? tones.map(String)
    : tones && typeof tones === 'object' ? Object.values(tones as Record<string, unknown>).map(String) : [];
  return { on: (f & 1) !== 0 || f === 0, off: (f & 2) !== 0 || f === 0, tones: (f & 4) !== 0 ? toneList : [],
    volume: (f & 8) !== 0, duration: (f & 16) !== 0 };
}

/** A camera's own extras on its device: sirens, lights (a floodlight) and switches to show beside it. */
export function cameraExtras(siblings: HubEntity[]): HubEntity[] {
  return siblings.filter((s) => s.domain === 'siren' || s.domain === 'light'
    || (s.domain === 'switch' && /siren|light|flood|spot|alarm|privacy|record|detect/i.test(s.name)));
}

// -- power and energy -------------------------------------------------------------------------------------

/** The device's live power (W) and energy (kWh) readings, for a plug's panel. */
export function powerReadings(siblings: HubEntity[]): { power: HubEntity | null; energy: HubEntity | null } {
  const sensors = siblings.filter((s) => s.domain === 'sensor');
  return {
    power: sensors.find((s) => s.device_class === 'power' || s.unit === 'W' || s.unit === 'kW') ?? null,
    energy: sensors.find((s) => s.device_class === 'energy' || s.unit === 'kWh' || s.unit === 'Wh') ?? null,
  };
}

/** The device's TV remote, if it has one (keys go through remote.send_command). */
export function remoteOf(siblings: HubEntity[]): HubEntity | null {
  return siblings.find((s) => s.domain === 'remote') ?? null;
}

/**
 * Which of the device's entities a control already shows, so the detail does not list them twice:
 * a plug's power and energy readings, a TV's remote, a vacuum's map and its readings, a camera's
 * siren and light.
 */
export function usedSiblings(e: HubEntity, siblings: HubEntity[]): string[] {
  const kind = controlKind(e);
  const ids: (HubEntity | null)[] = [];
  if (kind === 'switch' || kind === 'light' || kind === 'fan') {
    const p = powerReadings(siblings);
    ids.push(p.power, p.energy);
  }
  if (kind === 'tv' || kind === 'speaker') ids.push(remoteOf(siblings));
  if (kind === 'vacuum') {
    const r = vacuumReadings(siblings);
    ids.push(mapImage(siblings), r.battery, ...r.errors, ...r.wear);
  }
  if (kind === 'camera') ids.push(...cameraExtras(siblings));
  return ids.filter((x): x is HubEntity => Boolean(x) && x!.entity_id !== e.entity_id).map((x) => x.entity_id);
}

export const isUnavailableState = (s: string | undefined) => s === 'unavailable' || s === 'unknown';
