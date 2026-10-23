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

export interface PhoneUsagePluginInterface {
  hasAccess(): Promise<PhoneUsageAccess>;
  openSettings(): Promise<void>;
  getToday(): Promise<PhoneUsageToday>;
}

/* On the web (and old APKs without the plugin) there is simply no reading to
   take -- never fabricate one. */
const webFallback: PhoneUsagePluginInterface = {
  hasAccess: async () => ({ granted: false }),
  openSettings: async () => undefined,
  getToday: async () => ({ millis: 0, firstUse: null, lastUse: null, sessions: 0, apps: [] }),
};

const PhoneUsage = registerPlugin<PhoneUsagePluginInterface>('PhoneUsage', {
  web: () => webFallback,
});

export default PhoneUsage;
