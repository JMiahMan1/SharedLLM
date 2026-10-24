import { registerPlugin } from '@capacitor/core';

export interface SystemBarsPluginInterface {
  /**
   * Dark status-bar icons for a light theme, white ones for a dark theme, and
   * the strip behind the bars painted the page's own background (#rrggbb).
   */
  setScheme(options: { scheme: 'light' | 'dark'; background?: string }): Promise<void>;
}

/** `rgb(3, 7, 17)` / `rgba(...)` as `#030711`; anything else gives undefined. */
export function cssColorToHex(color: string): string | undefined {
  const m = /^rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+))?\s*\)$/.exec(color.trim());
  if (!m || (m[4] !== undefined && Number(m[4]) === 0)) return undefined;
  return `#${[m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, '0')).join('')}`;
}

// Not 'SystemBars': Capacitor 8 ships its own plugin under that name, and the
// two collided.
const SystemBars = registerPlugin<SystemBarsPluginInterface>('JarvisSystemBars', {
  web: { setScheme: async () => undefined },
});

export default SystemBars;
