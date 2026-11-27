/**
 * One way to ask for the assistant, from anywhere.
 *
 * The Quick Settings tile, the home-screen widget, a Bluetooth voice button and
 * Jarvis as the phone's Digital Assistant all arrive as the address
 * `/?voice=1`. That address is not a page, so the app used to navigate to `/`
 * and stop there: the trigger looked dead.
 *
 * This is the signal the shell passes to the assistant sheet instead. It is a
 * tiny module rather than a context because Android's native bridge, an OTA
 * deep link and a button on any page all need to raise it, and none of them are
 * inside the sheet's tree.
 */

const listeners = new Set<() => void>();

/**
 * Set while a request is waiting for the sheet to mount.
 *
 * A button in the header can be pressed on `/media`, where the sheet does not
 * exist yet; the press has to survive the navigation to `/` instead of being
 * shouted at nobody.
 */
let pending = false;

/** Open the assistant now (the sheet decides how, and where it sits). */
export function openVoiceAssistant(): void {
  pending = true;
  for (const listener of [...listeners]) {
    try {
      listener();
    } catch {
      // One screen failing to open the sheet must not stop the others.
    }
  }
}

/**
 * Claim a request made before the sheet existed, if there is one.
 *
 * A sheet that mounts reads this once, so a tap on another page still opens
 * the assistant, and a second mount does not reopen an already-dismissed one.
 */
export function takePendingVoiceRequest(): boolean {
  const waiting = pending;
  pending = false;
  return waiting;
}

/** Listen for that request. Returns the unsubscribe. */
export function onVoiceAssistantRequested(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Does this in-app address mean "the assistant" rather than a page?
 *
 * `/?voice=1` is what the Android triggers carry; `/assistant` is the friendly
 * name a link or a kiosk bookmark may use.
 */
export function isVoicePath(path: string): boolean {
  if (typeof path !== 'string' || !path.startsWith('/')) return false;
  try {
    const url = new URL(path, 'https://jarvis.local');
    if (url.pathname === '/assistant' || url.pathname === '/assistant/') return true;
    return url.searchParams.get('voice') === '1';
  } catch {
    return false;
  }
}

/** The same question for the address the app was opened at (a cold start, an OTA reload). */
export function voiceRequestedHere(search: string, hash: string): boolean {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  if (params.get('voice') === '1') return true;
  const route = hash.replace(/^#\/?/, '').replace(/\/$/, '');
  return route === 'assistant';
}
