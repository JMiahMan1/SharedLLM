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
  watch: 'Watch',
  assistant: 'Assistant',
  light: 'Light',
};

const NOUNS: Record<string, string> = {
  phone: 'phone',
  tablet: 'tablet',
  watch: 'watch',
  assistant: 'voice assistant',
  light: 'light',
};

const ESPHOME_KINDS = new Set(['watch', 'assistant', 'light']);
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
  if (platform === 'esphome') return true;
  if (platform === 'android') return false;
  const kind = (device.kind ?? '').trim().toLowerCase();
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

export function devicePlatformLabel(device: DevicePlatformLike | null | undefined): 'ESPHome' | 'Android' {
  return isEsphomeDevice(device) ? 'ESPHome' : 'Android';
}

export function esphomeTargetName(device: DevicePlatformLike): string {
  const label = (device.label ?? '').trim();
  if (label) return label;
  return (device.device_key ?? '').trim();
}
