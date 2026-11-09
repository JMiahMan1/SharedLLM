import { registerPlugin } from '@capacitor/core';

export interface Credentials {
  apiKey: string | null;
  serverUrl: string | null;
}

export interface TokenBridgePluginInterface {
  setCredentials(options: {
    apiKey?: string;
    serverUrl?: string;
  }): Promise<void>;
  getCredentials(): Promise<Credentials>;
  /** Mirror the signed-in identity so a background service can attribute uploads. */
  setIdentity(options: { username: string; userId?: string }): Promise<void>;
  /**
   * Mirror this phone's registration key so the screen guard can name itself on
   * its check-in and be handed the notices queued for this device alone.
   */
  setDeviceKey(options: { deviceKey: string }): Promise<void>;
  setLastLocation(options: { latitude: number; longitude: number }): Promise<void>;
  setHome(options: { latitude: number; longitude: number }): Promise<void>;
  refreshWidgets(): Promise<void>;
  setTheme(options: {
    themeId: string;
    bg: string;
    surface: string;
    text: string;
    textMuted: string;
    border: string;
    accent: string;
    onAccent: string;
    accentAlt?: string;
    scheme?: string;
  }): Promise<void>;
}

const webFallback: TokenBridgePluginInterface = {
  setCredentials: async () => undefined,
  getCredentials: async () => ({ apiKey: null, serverUrl: null }),
  setIdentity: async () => undefined,
  setDeviceKey: async () => undefined,
  setLastLocation: async () => undefined,
  setHome: async () => undefined,
  refreshWidgets: async () => undefined,
  setTheme: async () => undefined,
};

const TokenBridge = registerPlugin<TokenBridgePluginInterface>('TokenBridge', {
  web: webFallback,
});

export default TokenBridge;
