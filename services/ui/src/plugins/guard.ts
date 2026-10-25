import { registerPlugin } from '@capacitor/core';

export interface GuardStatus {
  /** Device admin: Jarvis cannot be uninstalled while it is on. */
  adminActive: boolean;
  /** Device owner (USB setup): the uninstall is blocked outright. */
  deviceOwner: boolean;
  /** The Accessibility screen guard is switched on. */
  guardEnabled: boolean;
  /** Android's VPN consent for the web filter was given (APKs after 437). */
  webFilterAllowed?: boolean;
  /** The web filter is running now. */
  webFilterActive?: boolean;
  /** Android's strict Private DNS hostname, "" when none (it gets around the web filter). */
  privateDnsHost?: string;
  /** Names of installed apps that get around the web filter (a VPN, Tor, Opera...). */
  bypassApps?: string[];
}

export interface GuardPluginInterface {
  status(): Promise<GuardStatus>;
  enableAdmin(): Promise<void>;
  openGuardSettings(): Promise<void>;
  openAppInfo(): Promise<void>;
  allowWebFilter(): Promise<void>;
}

// Off Android nothing is guarded: report it plainly, never a fake "on".
const webFallback: GuardPluginInterface = {
  status: async () => ({ adminActive: false, deviceOwner: false, guardEnabled: false }),
  enableAdmin: async () => undefined,
  openGuardSettings: async () => undefined,
  openAppInfo: async () => undefined,
  allowWebFilter: async () => undefined,
};

const Guard = registerPlugin<GuardPluginInterface>('JarvisGuard', { web: () => webFallback });

export default Guard;
