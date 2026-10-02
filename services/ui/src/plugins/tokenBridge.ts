import { registerPlugin } from '@capacitor/core';

export interface Credentials {
  apiKey: string | null;
  serverUrl: string | null;
}

export interface TokenBridgePluginInterface {
  setCredentials(options: {
    apiKey?: string;
    serverUrl?: string;
    internalSecret?: string;
  }): Promise<void>;
  getCredentials(): Promise<Credentials>;
  /** Mirror the signed-in identity so a background service can attribute uploads. */
  setIdentity(options: { username: string; userId?: string }): Promise<void>;
  setLastLocation(options: { latitude: number; longitude: number }): Promise<void>;
  setHome(options: { latitude: number; longitude: number }): Promise<void>;
  refreshWidgets(): Promise<void>;
}

const webFallback: TokenBridgePluginInterface = {
  setCredentials: async () => undefined,
  getCredentials: async () => ({ apiKey: null, serverUrl: null }),
  setIdentity: async () => undefined,
  setLastLocation: async () => undefined,
  setHome: async () => undefined,
  refreshWidgets: async () => undefined,
};

const TokenBridge = registerPlugin<TokenBridgePluginInterface>('TokenBridge', {
  web: webFallback,
});

export default TokenBridge;
