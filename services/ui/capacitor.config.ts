import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.jarvisos.app',
  appName: 'Jarvis OS',
  webDir: 'dist',
  bundledWebRuntime: false,
  server: {
    androidScheme: 'https',
    cleartext: true
  },
  plugins: {
    CapacitorHttp: {
      enabled: true
    },
    CapacitorCookies: {
      enabled: true
    },
    CapacitorUpdater: {
      autoUpdate: false,
      // Drop OTA bundles when a newer native APK is installed so stale web
      // assets never override the APK's bundled build.
      resetWhenUpdate: true
    }
  }
};

export default config;
