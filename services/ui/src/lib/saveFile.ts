import { Capacitor, registerPlugin } from '@capacitor/core';
import { blobToBase64 } from './base64';

/**
 * Saving a downloaded file. In a browser this is an ordinary download. In the
 * Android app it is not: the WebView ignores a `download` link to a `blob:`
 * URL, so every Download button did nothing there. The app instead hands the
 * bytes to SaveFilePlugin.java, which opens Android's "Save to" picker with
 * the name filled in.
 */
interface SaveFilePlugin {
  save(options: { data: string; filename: string; mimeType: string }): Promise<{ saved: boolean }>;
}

let plugin: SaveFilePlugin | null = null;

function nativeSaver(): SaveFilePlugin | null {
  if (Capacitor.getPlatform() !== 'android') return null;
  plugin ??= registerPlugin<SaveFilePlugin>('JarvisSaveFile');
  return plugin;
}

/** The last part of a path, so a nested artifact saves under its own name. */
function baseName(filename: string): string {
  return filename.split('/').pop() || 'download';
}

function anchorDownload(url: string, filename: string) {
  const a = document.createElement('a');
  a.href = url;
  a.download = baseName(filename);
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** An app build from before the plugin answers "not implemented". */
function notImplemented(err: unknown): boolean {
  const code = (err as { code?: string } | null)?.code;
  return code === 'UNIMPLEMENTED' || code === 'UNAVAILABLE';
}

/**
 * Save a blob as a file. Resolves false when the person cancelled the
 * picker, and rejects with the reason when the file could not be saved.
 */
export async function saveBlob(blob: Blob, filename: string): Promise<boolean> {
  const native = nativeSaver();
  if (native) {
    try {
      const { saved } = await native.save({
        data: await blobToBase64(blob),
        filename: baseName(filename),
        mimeType: blob.type || 'application/octet-stream',
      });
      return saved;
    } catch (err) {
      if (!notImplemented(err)) throw err;
    }
  }
  const url = URL.createObjectURL(blob);
  anchorDownload(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
  return true;
}

/**
 * Save the file behind an object URL the caller already made. In the app the
 * bytes are read back from the URL and saved natively; the caller must keep
 * the URL alive for a moment (every caller revokes later or never).
 */
export async function saveObjectUrl(url: string, filename: string): Promise<void> {
  if (!nativeSaver()) {
    anchorDownload(url, filename);
    return;
  }
  const blob = await (await fetch(url)).blob();
  await saveBlob(blob, filename);
}
