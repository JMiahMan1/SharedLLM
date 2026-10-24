// Display helpers for phone usage (package names, durations).

/** Friendly names for packages families actually see; the rest are tidied. */
const APP_NAMES: Record<string, string> = {
  'com.google.android.youtube': 'YouTube',
  'com.google.android.apps.youtube.kids': 'YouTube Kids',
  'com.spotify.music': 'Spotify',
  'com.android.chrome': 'Chrome',
  'com.instagram.android': 'Instagram',
  'com.zhiliaoapp.musically': 'TikTok',
  'com.snapchat.android': 'Snapchat',
  'com.whatsapp': 'WhatsApp',
  'com.roblox.client': 'Roblox',
  'com.mojang.minecraftpe': 'Minecraft',
  'com.netflix.mediaclient': 'Netflix',
  'com.google.android.apps.messaging': 'Messages',
  'com.android.vending': 'Play Store',
  'com.jarvisos.app': 'Jarvis',
};

export function appName(pkg: string): string {
  if (APP_NAMES[pkg]) return APP_NAMES[pkg];
  const last = pkg.split('.').filter(Boolean).pop() || pkg;
  return last.charAt(0).toUpperCase() + last.slice(1);
}

export function duration(ms: number): string {
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}
