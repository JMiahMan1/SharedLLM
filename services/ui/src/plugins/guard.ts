import { registerPlugin } from '@capacitor/core';

export interface GuardStatus {
  /** Device admin: Jarvis cannot be uninstalled while it is on. */
  adminActive: boolean;
  /** Device owner (USB setup): the uninstall is blocked outright. */
  deviceOwner: boolean;
  /** The Accessibility screen guard is switched on. */
  guardEnabled: boolean;
}

export interface GuardPluginInterface {
  status(): Promise<GuardStatus>;
  enableAdmin(): Promise<void>;
  openGuardSettings(): Promise<void>;
  openAppInfo(): Promise<void>;
}

// Off Android nothing is guarded: report it plainly, never a fake "on".
const webFallback: GuardPluginInterface = {
  status: async () => ({ adminActive: false, deviceOwner: false, guardEnabled: false }),
  enableAdmin: async () => undefined,
  openGuardSettings: async () => undefined,
  openAppInfo: async () => undefined,
};

const Guard = registerPlugin<GuardPluginInterface>('JarvisGuard', { web: () => webFallback });

export default Guard;
