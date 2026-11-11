/**
 * The human name and platform helpers for a registered device's kind.
 *
 * The server's kinds are phone | tablet | assistant | light | watch (see
 * services/identity/models.py). A kind this UI does not know is reported as
 * "Unknown" rather than guessed at, so a new server kind never renders as,
 * say, "Phone".
 */
const LABELS: Record<string, string> = {
  phone: 'Phone',
  tablet: 'Tablet',
  'windows-pc': 'Windows PC',
  watch: 'Watch',
  speaker: 'Smart speaker',
  screen: 'Smart screen',
  assistant: 'Assistant',
  light: 'Light',
};

const NOUNS: Record<string, string> = {
  phone: 'phone',
  tablet: 'tablet',
  'windows-pc': 'Windows PC',
  watch: 'watch',
  speaker: 'smart speaker',
  screen: 'smart screen',
  assistant: 'voice assistant',
  light: 'light',
};

export const ESPHOME_KIND_OPTIONS = ['watch', 'speaker', 'screen', 'assistant', 'light'] as const;
export type EsphomeDeviceKind = (typeof ESPHOME_KIND_OPTIONS)[number];

const ESPHOME_KINDS = new Set<string>(ESPHOME_KIND_OPTIONS);
const ESPHOME_REGISTERED_BY = new Set(['paired', 'adopted', 'admin']);

export interface DevicePlatformLike {
  kind?: string | null;
  device_key?: string | null;
  label?: string | null;
  esphome_version?: string | null;
  hardware?: string | null;
  registered_by?: string | null;
  platform?: string | null;
}

export function deviceKindLabel(kind: string | null | undefined): string {
  const key = (kind ?? '').trim().toLowerCase();
  return LABELS[key] ?? 'Unknown';
}

export function deviceNoun(kind: string | null | undefined): string {
  const key = (kind ?? '').trim().toLowerCase();
  return NOUNS[key] ?? 'device';
}

export function isEsphomeDevice(device: DevicePlatformLike | null | undefined): boolean {
  if (!device) return false;
  const platform = (device.platform ?? '').trim().toLowerCase();
  const kind = (device.kind ?? '').trim().toLowerCase();
  if (platform === 'esphome') return true;
  if (kind === 'windows-pc') return false;
  if (platform === 'android') return false;
  const key = (device.device_key ?? '').trim().toLowerCase();
  const reg = (device.registered_by ?? '').trim().toLowerCase();
  return (
    ESPHOME_KINDS.has(kind) ||
    Boolean(device.esphome_version) ||
    key.startsWith('esphome:') ||
    ESPHOME_REGISTERED_BY.has(reg)
  );
}

export function isAndroidDevice(device: DevicePlatformLike | null | undefined): boolean {
  return Boolean(device) && !isEsphomeDevice(device);
}

export function devicePlatformLabel(device: DevicePlatformLike | null | undefined): 'ESPHome' | 'Android' | 'Windows PC' {
  if ((device?.kind ?? '').trim().toLowerCase() === 'windows-pc') return 'Windows PC';
  return isEsphomeDevice(device) ? 'ESPHome' : 'Android';
}

export function esphomeTargetName(device: DevicePlatformLike): string {
  const label = (device.label ?? '').trim();
  if (label) return label;
  return (device.device_key ?? '').trim();
}

/** What to call a person's own device(s) in a sentence. */
export type HandheldNoun = 'phone' | 'tablet' | 'phone or tablet' | 'device';

/**
 * The word for someone's phones and tablets, from the kinds registered to
 * them: "tablet" for a child with only a tablet, "phone or tablet" for
 * someone with both, and "device" when nothing is known (never "phone" by
 * default, which would call a tablet a phone).
 */
export function handheldNoun(kinds: readonly (string | null | undefined)[] | null | undefined): HandheldNoun {
  const set = new Set((kinds ?? []).map((k) => (k ?? '').trim().toLowerCase()));
  const phone = set.has('phone');
  const tablet = set.has('tablet');
  if (phone && tablet) return 'phone or tablet';
  if (phone) return 'phone';
  if (tablet) return 'tablet';
  return 'device';
}

/** The word for the device the app runs on: its form factor, or "device" in a browser. */
export function thisDeviceNoun(formFactor: string | null | undefined): 'phone' | 'tablet' | 'device' {
  const key = (formFactor ?? '').trim().toLowerCase();
  return key === 'phone' || key === 'tablet' ? key : 'device';
}
