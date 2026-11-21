import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.jarvisos.app',
  appName: 'Jarvis OS',
  webDir: 'dist',
  bundledWebRuntime: false,
  server: {
    androidScheme: 'https',
    cleartext: true
    // No allowNavigation: a listed host gets the native bridge and a link to it
    // replaces the app page. Every main-frame navigation outside the app opens
    // in the phone's browser (Capacitor's Bridge.launchIntent). The private
    // chat's inline YouTube/Vimeo players keep their iframe navigations through
    // PlayerFrameWebViewClient (android/.../PlayerFrameRules.java), which allows
    // only subframes on those two hosts.
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
