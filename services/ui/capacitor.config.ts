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
    SystemBars: {
      // The page sets viewport-fit=cover; saying so up front stops Capacitor
      // padding the first frame and then re-laying it out edge to edge.
      insetsHandling: 'css',
      initialViewportFitValueHint: 'cover'
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
