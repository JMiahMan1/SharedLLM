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

export interface PhoneUsagePluginInterface {
  hasAccess(): Promise<PhoneUsageAccess>;
  openSettings(): Promise<void>;
  getToday(): Promise<PhoneUsageToday>;
  /** APKs after 437; older builds reject, and the sleep estimate simply has no data. */
  getNight(options: { since: number; until: number }): Promise<PhoneUsageNight>;
}

/* On the web (and old APKs without the plugin) there is simply no reading to
   take -- never fabricate one. */
const webFallback: PhoneUsagePluginInterface = {
  hasAccess: async () => ({ granted: false }),
  openSettings: async () => undefined,
  getToday: async () => ({ millis: 0, firstUse: null, lastUse: null, sessions: 0, apps: [] }),
  getNight: async ({ since, until }) => ({ since, until, intervals: [] }),
};

const PhoneUsage = registerPlugin<PhoneUsagePluginInterface>('PhoneUsage', {
  web: () => webFallback,
});

export default PhoneUsage;
