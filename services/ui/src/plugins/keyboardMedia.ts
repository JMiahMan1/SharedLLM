import { Capacitor, registerPlugin, type PluginListenerHandle } from '@capacitor/core';
import toast from 'react-hot-toast';

/**
 * GIFs, stickers and pictures from the phone's keyboard (Gboard's Tenor GIFs,
 * the Samsung Keyboard's GIPHY GIFs and stickers).
 *
 * Android's WebView tells a keyboard that no text box takes pictures, so every
 * keyboard refused with "This app doesn't support GIF insertion here", in the
 * private chat and in every family chat alike. JarvisWebView.java now says a
 * message box takes them, and KeyboardMediaPlugin.java hands what the keyboard
 * inserts to this module as a "media" event. It is pasted into the focused box
 * as a file, so it goes exactly the way a pasted picture already goes: the
 * private chat encrypts it before it leaves the phone, a family chat attaches it.
 */
export interface KeyboardMediaEvent {
  mime?: string;
  name?: string;
  /** The file, base64. */
  data?: string;
  /** Set instead of the file when it could not be read: 'too_large' or 'unreadable'. */
  error?: string;
}

export interface KeyboardMediaPluginInterface {
  available(): Promise<{ available: boolean }>;
  addListener(event: 'media', listener: (event: KeyboardMediaEvent) => void): Promise<PluginListenerHandle>;
}

const registered = registerPlugin<KeyboardMediaPluginInterface>('JarvisKeyboardMedia');

/** The only types a keyboard's file is passed on as (the native side asks for these). */
export const KEYBOARD_MEDIA_TYPES = new Set(['image/gif', 'image/webp', 'image/png', 'image/jpeg']);

declare global {
  interface Window {
    /** Hermetic browser tests only (the dev server): what the Android plugin's "media" event does. */
    __jarvisKeyboardMedia?: (event: KeyboardMediaEvent) => boolean;
  }
}

/** The keyboard's file, or null when the event carries none of the allowed types. */
export function keyboardFile(event: KeyboardMediaEvent): File | null {
  const mime = (event.mime ?? '').toLowerCase();
  if (!KEYBOARD_MEDIA_TYPES.has(mime) || !event.data) return null;
  let binary: string;
  try {
    binary = atob(event.data);
  } catch {
    return null;
  }
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], event.name || `keyboard.${mime.slice('image/'.length)}`, { type: mime });
}

/**
 * Paste a file into `target` (the focused box). True when the box took it: a
 * box that handles pasted files cancels the paste, which a box that does not
 * (a search field, a note) leaves alone.
 */
export function pasteFile(file: File, target: Element | null): boolean {
  if (!(target instanceof HTMLElement) || !target.isConnected) return false;
  const data = new DataTransfer();
  data.items.add(file);
  const paste = new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true });
  target.dispatchEvent(paste);
  return paste.defaultPrevented;
}

/** One "media" event: into the focused box, or a clear word when it could not go anywhere. */
export function handleKeyboardMedia(event: KeyboardMediaEvent, target: Element | null = document.activeElement): boolean {
  if (event.error === 'too_large') {
    toast.error('That GIF is too large to send.', { id: 'keyboard-media' });
    return false;
  }
  const file = keyboardFile(event);
  if (!file) {
    toast.error('The keyboard sent a picture Jarvis could not read.', { id: 'keyboard-media' });
    return false;
  }
  if (pasteFile(file, target)) return true;
  toast.error('GIFs and stickers can only be sent in a chat message.', { id: 'keyboard-media' });
  return false;
}

let started: Promise<PluginListenerHandle | null> | null = null;

/** Listen for the keyboard's pictures, once, for the life of the app (Android app only). */
export function startKeyboardMedia(plugin: KeyboardMediaPluginInterface | null = null): Promise<PluginListenerHandle | null> {
  if (import.meta.env.DEV && typeof window !== 'undefined') window.__jarvisKeyboardMedia = (event) => handleKeyboardMedia(event);
  const source = plugin ?? (Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android' ? registered : null);
  if (!source) return Promise.resolve(null);
  if (!started) {
    started = source.addListener('media', (event) => void handleKeyboardMedia(event)).catch(() => {
      // An APK from before the plugin: the keyboard still refuses there, as it always did.
      started = null;
      return null;
    });
  }
  return started;
}

/** Tests only: forget the listener so the next start adds one again. */
export function resetKeyboardMediaForTests(): void {
  started = null;
}
