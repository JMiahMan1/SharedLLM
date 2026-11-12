import { registerPlugin, type PluginListenerHandle } from '@capacitor/core';

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

/**
 * Jarvis opened from the screen-time lock ("Open Jarvis" on the guard's lock):
 * whether that mode is on, and the sections a parent left open in it (the
 * server's ``locked_sections``; empty when it has not said).
 */
export interface LockedModeState {
  active: boolean;
  sections: string[];
}

export interface GuardPluginInterface {
  status(): Promise<GuardStatus>;
  /** Locked mode as it is now (APKs with locked mode; older ones reject). */
  lockedMode(): Promise<LockedModeState>;
  addListener(event: 'lockedMode', listener: (state: LockedModeState) => void): Promise<PluginListenerHandle>;
  enableAdmin(): Promise<void>;
  openGuardSettings(): Promise<void>;
  openAppInfo(): Promise<void>;
  allowWebFilter(): Promise<void>;
}

// Off Android nothing is guarded: report it plainly, never a fake "on".
const webFallback: GuardPluginInterface = {
  status: async () => ({ adminActive: false, deviceOwner: false, guardEnabled: false }),
  lockedMode: async () => ({ active: false, sections: [] }),
  addListener: async () => ({ remove: async () => undefined }),
  enableAdmin: async () => undefined,
  openGuardSettings: async () => undefined,
  openAppInfo: async () => undefined,
  allowWebFilter: async () => undefined,
};

const Guard = registerPlugin<GuardPluginInterface>('JarvisGuard', { web: () => webFallback });

export default Guard;
