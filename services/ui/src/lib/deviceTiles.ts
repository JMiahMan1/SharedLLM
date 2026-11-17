import type { HubEntity } from './dashboards';
import type { HardwareDevice, HardwareEntity } from './hardware';

/**
 * Devices > Your devices: what a device tile and a device's detail draw, decided from what the
 * hub says the device is (its kind) and what its main entity supports. Pure functions only, so
 * every decision here is unit-tested (deviceTiles.test.ts) and the components stay thin.
 */

/** What a tile looks like and which type chip it falls under. Finer than the hub's kind. */
export type TileKind = 'light' | 'switch' | 'fan' | 'tv' | 'speaker' | 'media' | 'climate' | 'cover' | 'lock'
  | 'vacuum' | 'camera' | 'sensor' | 'voice' | 'phone' | 'other';

/** The type chips, in order; each covers one or more tile kinds. */
export const TYPE_FILTERS: { id: string; label: string; kinds: TileKind[] }[] = [
  { id: 'lights', label: 'Lights', kinds: ['light'] },
  { id: 'switches', label: 'Plugs & switches', kinds: ['switch', 'fan'] },
  { id: 'media', label: 'TVs & speakers', kinds: ['tv', 'speaker', 'media', 'voice'] },
  { id: 'climate', label: 'Climate', kinds: ['climate'] },
  { id: 'access', label: 'Doors & locks', kinds: ['cover', 'lock'] },
  { id: 'cameras', label: 'Cameras', kinds: ['camera'] },
  { id: 'sensors', label: 'Sensors', kinds: ['sensor'] },
  { id: 'vacuums', label: 'Vacuums', kinds: ['vacuum'] },
  { id: 'phones', label: 'Phones', kinds: ['phone'] },
  { id: 'other', label: 'Other', kinds: ['other'] },
];

export function typeFilterOf(kind: TileKind): string {
  return TYPE_FILTERS.find((f) => f.kinds.includes(kind))?.id ?? 'other';
}

/** Which entity domain speaks for a device of each hub kind, most telling first. */
const PRIMARY_DOMAINS: Record<string, string[]> = {
  light: ['light', 'switch'],
  power: ['switch', 'fan', 'input_boolean', 'light'],
  media: ['media_player', 'remote'],
  climate: ['climate', 'water_heater', 'fan', 'humidifier'],
  access: ['lock', 'cover', 'valve'],
  vacuum: ['vacuum', 'lawn_mower'],
  camera: ['camera'],
  voice: ['assist_satellite', 'media_player'],
  sensor: ['binary_sensor', 'sensor'],
  phone: ['device_tracker', 'sensor'],
  other: ['light', 'switch', 'fan', 'media_player', 'climate', 'cover', 'lock', 'vacuum', 'camera', 'binary_sensor', 'sensor'],
};

/** Sensor classes worth a tile's state line, best first (a thermometer says its temperature). */
const SENSOR_CLASS_ORDER = ['temperature', 'humidity', 'power', 'energy', 'illuminance', 'battery'];

/** The entity that speaks for a device on its tile: its main control, or its most telling reading. */
export function primaryEntity(kind: string | undefined, entities: HubEntity[]): HubEntity | null {
  if (!entities.length) return null;
  const order = PRIMARY_DOMAINS[kind ?? 'other'] ?? PRIMARY_DOMAINS.other;
  for (const domain of order) {
    const same = entities.filter((e) => e.domain === domain);
    if (!same.length) continue;
    if (domain === 'sensor') {
      const ranked = [...same].sort((a, b) => rankClass(a.device_class) - rankClass(b.device_class));
      return ranked[0];
    }
    return same[0];
  }
  return entities[0];
}

function rankClass(c: string | null): number {
  const i = SENSOR_CLASS_ORDER.indexOf(c ?? '');
  return i === -1 ? SENSOR_CLASS_ORDER.length : i;
}

/** The hub's kind for an entity alone (a Rooms page entity has no device kind with it). */
const DOMAIN_KIND: Record<string, string> = {
  media_player: 'media', remote: 'media', climate: 'climate', water_heater: 'climate', fan: 'climate', cover: 'access',
  lock: 'access', valve: 'access', vacuum: 'vacuum', lawn_mower: 'vacuum', camera: 'camera', switch: 'power',
  sensor: 'sensor', binary_sensor: 'sensor', light: 'light', siren: 'power',
};

/** The tile's kind: the hub's kind, sharpened by the main entity (a TV is not a speaker). */
export function tileKind(device: Pick<HardwareDevice, 'model' | 'name' | 'manufacturer'> & { kind?: string },
  primary: Pick<HubEntity, 'domain' | 'device_class'> | null): TileKind {
  const k = device.kind ?? DOMAIN_KIND[primary?.domain ?? ''] ?? 'other';
  const domain = primary?.domain;
  if (k === 'light' || domain === 'light') return 'light';
  if (k === 'phone') return 'phone';
  if (k === 'voice') return 'voice';
  if (k === 'camera') return 'camera';
  if (k === 'vacuum') return 'vacuum';
  if (k === 'climate') return domain === 'fan' ? 'fan' : 'climate';
  if (k === 'access') return domain === 'lock' ? 'lock' : 'cover';
  if (k === 'media') {
    if (primary?.device_class === 'tv') return 'tv';
    if (primary?.device_class === 'speaker') return 'speaker';
    const words = `${device.name} ${device.model ?? ''} ${device.manufacturer ?? ''}`.toLowerCase();
    if (/\b(tv|television|roku|chromecast|apple tv|shield|fire ?stick|projector)\b/.test(words)) return 'tv';
    if (/\b(speaker|sonos|echo|nest (mini|audio)|homepod|soundbar|sendspin)\b/.test(words)) return 'speaker';
    return 'media';
  }
  if (k === 'power') return domain === 'fan' ? 'fan' : 'switch';
  if (k === 'sensor') return 'sensor';
  if (domain === 'switch' || domain === 'input_boolean') return 'switch';
  if (domain === 'fan') return 'fan';
  if (domain === 'sensor' || domain === 'binary_sensor') return 'sensor';
  return 'other';
}

const UNAVAILABLE = new Set(['unavailable', 'unknown']);
const CLIMATE_WORDS: Record<string, string> = { heat_cool: 'Heat and cool', fan_only: 'Fan only', heating: 'Heating',
  cooling: 'Cooling', drying: 'Drying', fan: 'Fan' };
export const isOff = (e: Pick<HubEntity, 'state'> | null) => !e || UNAVAILABLE.has(e.state);

/** Does the device answer? Offline devices are dimmed and their controls disabled. */
export function isOffline(device: Pick<HardwareDevice, 'status'>, primary: Pick<HubEntity, 'state'> | null): boolean {
  if (device.status === 'offline' || device.status === 'not_loaded' || device.status === 'disabled') return true;
  return primary != null && UNAVAILABLE.has(primary.state);
}

/** "3 min", "2 h", "4 days": how long, for "Offline for ...". */
export function duration(fromIso: string | null, now = Date.now()): string {
  if (!fromIso) return '';
  const t = new Date(fromIso).getTime();
  if (!Number.isFinite(t)) return '';
  const minutes = Math.max(1, Math.round((now - t) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

const pct = (b: unknown) => (typeof b === 'number' ? Math.max(1, Math.round((b / 255) * 100)) : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v
  : typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v)) ? Number(v) : null);
const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
export const words = (s: string) => cap(s.replace(/_/g, ' '));

/** A reading in words: "21.5 °C", "41 %", "Open". */
export function readingText(e: Pick<HubEntity, 'state' | 'unit' | 'domain' | 'device_class'>): string {
  if (e.domain === 'binary_sensor') return binaryText(e.state, e.device_class);
  const n = num(e.state);
  if (n != null) {
    const rounded = Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10;
    return e.unit ? `${rounded} ${e.unit}` : String(rounded);
  }
  return words(e.state);
}

function binaryText(state: string, cls: string | null): string {
  const on = state === 'on';
  switch (cls) {
    case 'door': case 'garage_door': case 'window': case 'opening': return on ? 'Open' : 'Closed';
    case 'motion': case 'occupancy': case 'presence': return on ? 'Motion' : 'Clear';
    case 'moisture': return on ? 'Wet' : 'Dry';
    case 'smoke': case 'gas': case 'carbon_monoxide': case 'problem': case 'safety': case 'tamper': return on ? 'Alert' : 'OK';
    default: return on ? 'On' : 'Off';
  }
}

/**
 * The tile's one plain line: "On, 71%", "Off", "21.5 °C", "Playing", "Locked", "Offline for 3 min".
 * An offline device says so first; that is what a person needs to know about it.
 */
export function stateLine(device: Pick<HardwareDevice, 'status' | 'last_seen'>, primary: HubEntity | null,
  now = Date.now()): string {
  if (device.status === 'disabled') return 'Turned off in settings';
  if (device.status === 'not_loaded') return 'Not connected';
  if (isOffline(device, primary)) {
    const since = primary && UNAVAILABLE.has(primary.state) ? primary.last_changed : device.last_seen;
    const d = duration(since, now);
    return d ? `Offline for ${d}` : 'Offline';
  }
  if (!primary) return device.status === 'partial' ? 'Partly offline' : 'Online';
  const a = primary.attributes ?? {};
  switch (primary.domain) {
    case 'light': {
      if (primary.state !== 'on') return 'Off';
      const p = pct(a.brightness);
      return p != null ? `On, ${p}%` : 'On';
    }
    case 'switch': case 'input_boolean': case 'fan': case 'humidifier': case 'siren': {
      if (primary.state !== 'on') return 'Off';
      const p = num(a.percentage);
      return primary.domain === 'fan' && p != null ? `On, ${p}%` : 'On';
    }
    case 'media_player': {
      if (primary.state === 'playing') {
        const title = typeof a.media_title === 'string' ? a.media_title : '';
        return title ? `Playing ${title}` : 'Playing';
      }
      if (primary.state === 'paused') return 'Paused';
      if (primary.state === 'off' || primary.state === 'standby') return 'Off';
      const app = typeof a.app_name === 'string' && a.app_name ? a.app_name : typeof a.source === 'string' ? a.source : '';
      return app ? `On, ${app}` : 'On';
    }
    case 'climate': {
      const target = num(a.temperature);
      const low = num(a.target_temp_low);
      const high = num(a.target_temp_high);
      const current = num(a.current_temperature);
      const unit = typeof a.temperature_unit === 'string' ? a.temperature_unit : '°';
      if (primary.state === 'off') return current != null ? `Off, ${current}${unit} now` : 'Off';
      const doing = typeof a.hvac_action === 'string' && !['idle', 'off'].includes(a.hvac_action) ? a.hvac_action : primary.state;
      const action = CLIMATE_WORDS[doing] ?? words(doing);
      if (target != null) return `${action} to ${target}${unit}`;
      if (low != null && high != null) return `${action}, ${low} to ${high}${unit}`;
      return action;
    }
    case 'cover': {
      const pos = num(a.current_position);
      if (primary.state === 'open' && pos != null && pos < 100) return `Open ${pos}%`;
      return words(primary.state);
    }
    case 'lock': return words(primary.state);
    case 'vacuum': case 'lawn_mower': {
      const battery = num(a.battery_level);
      return battery != null ? `${words(primary.state)}, ${battery}% battery` : words(primary.state);
    }
    case 'camera': return primary.state === 'recording' ? 'Recording' : primary.state === 'streaming' ? 'Live' : 'On';
    case 'sensor': case 'binary_sensor': return readingText(primary);
    case 'device_tracker': return primary.state === 'home' ? 'Home' : primary.state === 'not_home' ? 'Away' : words(primary.state);
    default: return words(primary.state);
  }
}

/** What one tap on a tile does, if anything. Locks and covers open the detail (they move things). */
export type TileAction = { kind: 'toggle'; on: boolean; service: string; label: string }
  | { kind: 'playpause'; on: boolean; service: string; label: string };

const TOGGLE_DOMAINS = new Set(['light', 'switch', 'fan', 'input_boolean', 'humidifier', 'siren']);

export function tileAction(primary: HubEntity | null, name: string): TileAction | null {
  if (!primary || UNAVAILABLE.has(primary.state)) return null;
  if (TOGGLE_DOMAINS.has(primary.domain)) {
    const on = primary.state === 'on';
    return { kind: 'toggle', on, service: on ? 'turn_off' : 'turn_on', label: `${name} power` };
  }
  if (primary.domain === 'media_player' && (primary.state === 'playing' || primary.state === 'paused')) {
    const on = primary.state === 'playing';
    return { kind: 'playpause', on, service: on ? 'media_pause' : 'media_play', label: `${on ? 'Pause' : 'Play'} ${name}` };
  }
  // A TV that is on or off: its power, when it can be switched (TURN_ON 128 | TURN_OFF 256).
  if (primary.domain === 'media_player' && ((num(primary.attributes?.supported_features) ?? 0) & 384) !== 0) {
    const on = !['off', 'standby'].includes(primary.state);
    return { kind: 'toggle', on, service: on ? 'turn_off' : 'turn_on', label: `${name} power` };
  }
  return null;
}

/** The state a command is expected to leave, drawn at once and rolled back if it fails. */
export function expectedState(entity: HubEntity, service: string, data?: Record<string, unknown>): Partial<HubEntity> | null {
  const attrs: Record<string, unknown> = {};
  switch (service) {
    case 'turn_on': {
      if (data?.brightness_pct != null) attrs.brightness = Math.round((Number(data.brightness_pct) / 100) * 255);
      if (data?.color_temp_kelvin != null) { attrs.color_temp_kelvin = data.color_temp_kelvin; attrs.color_mode = 'color_temp'; }
      if (data?.rgb_color != null) { attrs.rgb_color = data.rgb_color; attrs.color_mode = 'rgb'; }
      if (data?.hs_color != null) { attrs.hs_color = data.hs_color; attrs.color_mode = 'hs'; }
      if (data?.effect != null) attrs.effect = data.effect;
      return { state: 'on', attributes: { ...entity.attributes, ...attrs } };
    }
    case 'turn_off': return { state: 'off' };
    case 'toggle': return { state: entity.state === 'on' ? 'off' : 'on' };
    case 'media_play': return { state: 'playing' };
    case 'media_pause': return { state: 'paused' };
    case 'volume_set': return { attributes: { ...entity.attributes, volume_level: data?.volume_level } };
    case 'select_source': return { attributes: { ...entity.attributes, source: data?.source } };
    case 'open_cover': return { state: 'opening' };
    case 'close_cover': return { state: 'closing' };
    case 'set_cover_position': return { attributes: { ...entity.attributes, current_position: data?.position } };
    case 'lock': return { state: 'locking' };
    case 'unlock': return { state: 'unlocking' };
    case 'set_percentage': return { state: Number(data?.percentage) > 0 ? 'on' : 'off',
      attributes: { ...entity.attributes, percentage: data?.percentage } };
    case 'volume_mute': return { attributes: { ...entity.attributes, is_volume_muted: data?.is_volume_muted } };
    case 'set_hvac_mode': return { state: String(data?.hvac_mode ?? entity.state) };
    case 'set_temperature': return { attributes: { ...entity.attributes, ...(data ?? {}) } };
    case 'set_fan_mode': return { attributes: { ...entity.attributes, fan_mode: data?.fan_mode } };
    case 'set_preset_mode': return { attributes: { ...entity.attributes, preset_mode: data?.preset_mode } };
    case 'set_swing_mode': return { attributes: { ...entity.attributes, swing_mode: data?.swing_mode } };
    case 'set_fan_speed': return { attributes: { ...entity.attributes, fan_speed: data?.fan_speed } };
    case 'oscillate': return { attributes: { ...entity.attributes, oscillating: data?.oscillating } };
    case 'install': return { attributes: { ...entity.attributes, in_progress: true } };
    case 'skip': return { state: 'off', attributes: { ...entity.attributes, skipped_version: entity.attributes?.latest_version } };
    case 'clear_skipped': return { state: 'on', attributes: { ...entity.attributes, skipped_version: null } };
    case 'start': return { state: entity.domain === 'lawn_mower' ? 'mowing' : 'cleaning' };
    case 'pause': return { state: 'paused' };
    case 'return_to_base': return { state: 'returning' };
    case 'set_value': case 'select_option': return { state: String(data?.value ?? data?.option ?? entity.state) };
    default: return null;
  }
}

/** An entity with a pending change drawn over it. */
export function withChange(entity: HubEntity, change: Partial<HubEntity> | null | undefined): HubEntity {
  if (!change) return entity;
  return { ...entity, ...change, attributes: { ...entity.attributes, ...(change.attributes ?? {}) } };
}

// -- lights ------------------------------------------------------------------------------------

export interface LightFeatures {
  brightness: boolean;
  colorTemp: boolean;
  color: boolean;
  /** Warmest and coolest white the bulb makes, in kelvin. */
  minKelvin: number;
  maxKelvin: number;
}

const COLOR_MODES = new Set(['hs', 'xy', 'rgb', 'rgbw', 'rgbww']);

/** What a light's detail offers: from supported_color_modes (Home Assistant's own contract). */
export function lightFeatures(e: Pick<HubEntity, 'attributes' | 'abilities'>): LightFeatures {
  const a = e.attributes ?? {};
  const modes = Array.isArray(a.supported_color_modes) ? (a.supported_color_modes as string[]) : null;
  const abilities = Array.isArray(e.abilities) ? e.abilities : null;
  const colorTemp = modes ? modes.includes('color_temp') : Boolean(abilities?.includes('color_temp'));
  const color = modes ? modes.some((m) => COLOR_MODES.has(m)) : Boolean(abilities?.includes('color'));
  const brightness = modes ? modes.some((m) => m !== 'onoff') : (abilities ? abilities.includes('dim')
    : typeof a.brightness === 'number');
  let minKelvin = num(a.min_color_temp_kelvin) ?? (num(a.max_mireds) ? Math.round(1e6 / num(a.max_mireds)!) : null) ?? 2000;
  let maxKelvin = num(a.max_color_temp_kelvin) ?? (num(a.min_mireds) ? Math.round(1e6 / num(a.min_mireds)!) : null) ?? 6500;
  if (minKelvin > maxKelvin) [minKelvin, maxKelvin] = [maxKelvin, minKelvin];
  return { brightness, colorTemp, color, minKelvin, maxKelvin };
}

/** A colour temperature as an RGB a screen can show (Tanner Helland's approximation). */
export function kelvinToRgb(kelvin: number): [number, number, number] {
  const t = Math.max(1000, Math.min(40000, kelvin)) / 100;
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  const r = t <= 66 ? 255 : 329.698727446 * (t - 60) ** -0.1332047592;
  const g = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * (t - 60) ** -0.0755148492;
  const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  return [clamp(r), clamp(g), clamp(b)];
}

/** Hue/saturation (0-360, 0-100) to RGB. */
export function hsToRgb(h: number, s: number): [number, number, number] {
  const sat = s / 100;
  const f = (n: number) => {
    const k = (n + h / 60) % 6;
    return Math.round(255 * (1 - sat * Math.max(0, Math.min(k, 4 - k, 1))));
  };
  return [f(5), f(3), f(1)];
}

export const rgbCss = ([r, g, b]: [number, number, number]) => `rgb(${r}, ${g}, ${b})`;
export const rgbHex = (rgb: [number, number, number]) => `#${rgb.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
export function hexToRgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
  return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
}

const isRgb = (v: unknown): v is [number, number, number] => Array.isArray(v) && v.length >= 3 && v.every((x) => typeof x === 'number');

/** The colour a lit light is showing, for its tile's accent; null when off or unknown. */
export function lightColour(e: HubEntity | null): [number, number, number] | null {
  if (!e || e.domain !== 'light' || e.state !== 'on') return null;
  const a = e.attributes ?? {};
  const mode = typeof a.color_mode === 'string' ? a.color_mode : null;
  if (mode !== 'color_temp' && isRgb(a.rgb_color)) return [a.rgb_color[0], a.rgb_color[1], a.rgb_color[2]];
  if (mode !== 'color_temp' && Array.isArray(a.hs_color) && a.hs_color.length === 2) {
    return hsToRgb(Number(a.hs_color[0]), Number(a.hs_color[1]));
  }
  const k = num(a.color_temp_kelvin) ?? (num(a.color_temp) ? Math.round(1e6 / num(a.color_temp)!) : null);
  if (k) return kelvinToRgb(k);
  if (isRgb(a.rgb_color)) return [a.rgb_color[0], a.rgb_color[1], a.rgb_color[2]];
  return null;
}

/** Colour presets for the palette: what people pick most, plus warm and cool whites when the bulb has them. */
export const COLOUR_PRESETS: { label: string; rgb: [number, number, number] }[] = [
  { label: 'Red', rgb: [255, 59, 48] },
  { label: 'Orange', rgb: [255, 149, 0] },
  { label: 'Yellow', rgb: [255, 214, 10] },
  { label: 'Green', rgb: [52, 199, 89] },
  { label: 'Teal', rgb: [48, 200, 200] },
  { label: 'Blue', rgb: [10, 132, 255] },
  { label: 'Indigo', rgb: [94, 92, 230] },
  { label: 'Purple', rgb: [175, 82, 222] },
  { label: 'Pink', rgb: [255, 55, 145] },
];

// -- media -------------------------------------------------------------------------------------

/** HA media_player supported_features bits. */
const MP = { PAUSE: 1, VOLUME_SET: 4, VOLUME_MUTE: 8, PREVIOUS: 16, NEXT: 32, TURN_ON: 128, TURN_OFF: 256,
  SELECT_SOURCE: 2048, STOP: 4096, PLAY: 16384 };

export interface MediaFeatures {
  power: boolean; play: boolean; previous: boolean; next: boolean; volume: boolean; mute: boolean; sources: string[];
}

export function mediaFeatures(e: Pick<HubEntity, 'attributes' | 'abilities'>): MediaFeatures {
  const a = e.attributes ?? {};
  const f = num(a.supported_features);
  const ab = Array.isArray(e.abilities) ? e.abilities : null;
  const has = (bit: number, ability: string, fallback: boolean) => (f != null ? (f & bit) !== 0
    : ab ? ab.includes(ability) : fallback);
  const sources = Array.isArray(a.source_list) ? (a.source_list as unknown[]).map(String) : [];
  return {
    power: has(MP.TURN_ON | MP.TURN_OFF, 'power', true),
    play: has(MP.PLAY | MP.PAUSE, 'play', true),
    previous: has(MP.PREVIOUS, 'previous', false),
    next: has(MP.NEXT, 'next', false),
    volume: has(MP.VOLUME_SET, 'volume', typeof a.volume_level === 'number'),
    mute: has(MP.VOLUME_MUTE, 'mute', typeof a.is_volume_muted === 'boolean'),
    sources: has(MP.SELECT_SOURCE, 'select_source', sources.length > 0) ? sources : [],
  };
}

// -- the detail's sections ---------------------------------------------------------------------

/** Controls, sensors, configuration and diagnostics, like Home Assistant's device page, in plain words. */
export type EntitySection = 'controls' | 'sensors' | 'config' | 'diagnostic' | 'disabled';

const READING_DOMAINS = new Set(['sensor', 'binary_sensor', 'event', 'device_tracker', 'weather']);

export function entitySection(e: Pick<HardwareEntity, 'domain' | 'entity_category' | 'disabled_by'>): EntitySection {
  if (e.disabled_by) return 'disabled';
  if (e.entity_category === 'config') return 'config';
  if (e.entity_category === 'diagnostic') return READING_DOMAINS.has(e.domain) ? 'diagnostic' : 'config';
  return READING_DOMAINS.has(e.domain) ? 'sensors' : 'controls';
}

/**
 * An entity's name without its device's name in front: on the Kauf bulb's page "Kauf Bulb Default
 * fade" is just "Default fade". A name that is only the device's name is the device itself.
 */
export function plainName(entityName: string, deviceName: string): string {
  const n = entityName.trim();
  const d = deviceName.trim();
  if (!d) return n;
  if (n.toLowerCase() === d.toLowerCase()) return n;
  if (n.toLowerCase().startsWith(`${d.toLowerCase()} `)) {
    const rest = n.slice(d.length).trim();
    return rest ? rest.charAt(0).toUpperCase() + rest.slice(1) : n;
  }
  return n;
}

/** Numeric history points of one entity, for a small chart; empty when it is not a number. */
export function numericHistory(points: { t: string; s: string }[]): { t: number; v: number }[] {
  const out: { t: number; v: number }[] = [];
  for (const p of points) {
    const v = Number(p.s);
    const t = new Date(p.t).getTime();
    if (p.s !== '' && Number.isFinite(v) && Number.isFinite(t)) out.push({ t, v });
  }
  return out;
}
