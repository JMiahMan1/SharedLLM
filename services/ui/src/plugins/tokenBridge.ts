import { registerPlugin } from '@capacitor/core';

export interface Credentials {
  apiKey: string | null;
  serverUrl: string | null;
}

export interface TokenBridgePluginInterface {
  setCredentials(options: { apiKey?: string; serverUrl?: string }): Promise<void>;
  getCredentials(): Promise<Credentials>;
  setLastLocation(options: { latitude: number; longitude: number }): Promise<void>;
  setHome(options: { latitude: number; longitude: number }): Promise<void>;
  refreshWidgets(): Promise<void>;
}

const webFallback: TokenBridgePluginInterface = {
  setCredentials: async () => undefined,
  getCredentials: async () => ({ apiKey: null, serverUrl: null }),
  setLastLocation: async () => undefined,
  setHome: async () => undefined,
  refreshWidgets: async () => undefined,
};

const TokenBridge = registerPlugin<TokenBridgePluginInterface>('TokenBridge', {
  web: webFallback,
});

export default TokenBridge;
