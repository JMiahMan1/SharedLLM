import { registerPlugin } from '@capacitor/core';

/** The mobile form factors a device may be; the server accepts no other. */
export type MobileFormFactor = 'phone' | 'tablet';

export interface DeviceInfoPluginInterface {
  /**
   * "phone" or "tablet" from Android's smallest screen width (600dp and up is
   * a tablet), or null when the platform reported no width. Never guessed.
   */
  getFormFactor(): Promise<{ formFactor: MobileFormFactor | null }>;
}

/* On the web there is no Android configuration to read, so the form factor is
   unknown rather than assumed. */
const webFallback: DeviceInfoPluginInterface = {
  getFormFactor: async () => ({ formFactor: null }),
};

const DeviceInfo = registerPlugin<DeviceInfoPluginInterface>('JarvisDeviceInfo', {
  web: () => webFallback,
});

export default DeviceInfo;
