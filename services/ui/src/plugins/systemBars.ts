import { registerPlugin } from '@capacitor/core';

export interface SystemBarsPluginInterface {
  /** Dark status-bar icons for a light theme, white ones for a dark theme. */
  setScheme(options: { scheme: 'light' | 'dark' }): Promise<void>;
}

const SystemBars = registerPlugin<SystemBarsPluginInterface>('SystemBars', {
  web: { setScheme: async () => undefined },
});

export default SystemBars;
