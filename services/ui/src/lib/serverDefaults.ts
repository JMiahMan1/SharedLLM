/**
 * The gateway a native build talks to before the user has entered one.
 *
 * It comes from the build environment (`VITE_DEFAULT_SERVER_URL`), never from
 * the source, so a fork or a public checkout ships no household address. When
 * it is unset the app has no default and asks for a server URL on first run.
 * Kept free of imports so storage.ts and serverUrl.ts can both use it without
 * an import cycle.
 */
function readDefaultServerUrl(): string {
  const raw = (import.meta.env.VITE_DEFAULT_SERVER_URL as string | undefined) ?? '';
  return raw.trim().replace(/\/+$/, '');
}

export const DEFAULT_SERVER_URL: string = readDefaultServerUrl();

/** Example shown in server URL fields; never used as an address. */
export const SERVER_URL_EXAMPLE = 'https://jarvis.example.com';
