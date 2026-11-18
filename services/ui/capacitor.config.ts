import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.jarvisos.app',
  appName: 'Jarvis OS',
  webDir: 'dist',
  bundledWebRuntime: false,
  server: {
    androidScheme: 'https',
    cleartext: true,
    // The private chat's link previews play a YouTube or Vimeo video inline, in
    // an iframe, once tapped (PrivateLinkCard). Capacitor sends a navigation to
    // any host outside the app to the phone's browser, and Android can report
    // an iframe's load as one; these two player hosts stay in the WebView.
    allowNavigation: ['www.youtube-nocookie.com', 'player.vimeo.com']
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
