import { registerPlugin } from '@capacitor/core';

/* The phone's own usage screen time (Android UsageStatsManager). Access is a
   special permission the person grants in Settings, so this never prompts. */

export interface PhoneUsageAccess {
  granted: boolean;
}

export interface PhoneUsageApp {
  package: string;
  millis: number;
  lastUse: number | null;
}

export interface PhoneUsageToday {
  millis: number;
  firstUse: number | null;
  lastUse: number | null;
  sessions: number;
  apps: PhoneUsageApp[];
}

/** Stretches the screen was in use, as [start, end] epoch ms pairs (no app names). */
export interface PhoneUsageNight {
  since: number;
  until: number;
  intervals: Array<[number, number]>;
}

/** One app a person installed (never system apps, never Jarvis), for app vetting. */
export interface InstalledApp {
  package: string;
  label: string;
  /** The store that installed it ("com.android.vending" is Google Play), or null for a sideload. */
  installer: string | null;
  first_install: number | null;
  last_update: number | null;
  launchable: boolean;
  /** Dangerous permissions it asks for, as groups: location, camera, microphone... */
  permissions: string[];
  /** Last use (needs usage access), else null. */
  last_used: number | null;
  size_bytes: number | null;
  /** Risky access it declares or asks for (APKs with the security scan): accessibility, device_admin,
   *  notification_listener, vpn, sms_read, sms_send, call_log, background_location, overlay, install_apps. */
  capabilities?: string[];
  /** Special access switched on for it now: accessibility, device_admin, notification_listener. */
  special_access?: string[];
  /** SHA-1 fingerprints of its signing certificates (uppercase hex), for the stalkerware list. */
  cert_sha1?: string[];
  /** SHA-256 of its base APK (lowercase hex), or null when not hashed (see apk_hash_state). */
  apk_sha256?: string | null;
  /** "ok", or why there is no hash: "too_large", "unreadable", "deferred" (a later report hashes it). */
  apk_hash_state?: 'ok' | 'too_large' | 'unreadable' | 'deferred' | null;
}

export interface PhoneUsagePluginInterface {
  hasAccess(): Promise<PhoneUsageAccess>;
  openSettings(): Promise<void>;
  getToday(): Promise<PhoneUsageToday>;
  /** APKs after 437; older builds reject, and the sleep estimate simply has no data. */
  getNight(options: { since: number; until: number }): Promise<PhoneUsageNight>;
  /** The installed apps (APKs with app vetting; older builds reject and nothing is sent). */
  getInstalledApps(): Promise<{ apps: InstalledApp[]; usageAccess: boolean }>;
}

/* On the web (and old APKs without the plugin) there is simply no reading to
   take -- never fabricate one. */
const webFallback: PhoneUsagePluginInterface = {
  hasAccess: async () => ({ granted: false }),
  openSettings: async () => undefined,
  getToday: async () => ({ millis: 0, firstUse: null, lastUse: null, sessions: 0, apps: [] }),
  getNight: async ({ since, until }) => ({ since, until, intervals: [] }),
  // No device to read: refuse rather than report an empty phone.
  getInstalledApps: async () => {
    throw new Error('Installed apps are only readable on Android');
  },
};

const PhoneUsage = registerPlugin<PhoneUsagePluginInterface>('PhoneUsage', {
  web: () => webFallback,
});

export default PhoneUsage;
