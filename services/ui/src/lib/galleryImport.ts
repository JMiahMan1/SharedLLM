/**
 * Adding pictures straight to the private gallery (no chat message), and the
 * bulk import of an export from the Between couples app. Kept free of React so
 * it can be tested directly.
 *
 * The picture's bytes are kept exactly as they were (EXIF and all, inside the
 * encryption). What the gallery needs to place it (when it was taken, its name,
 * the camera, where) is read here and travels in the sealed entry, never in the
 * clear. A SHA-256 of the original bytes travels in it too, so an import that
 * stopped half way can be run again and skips what is already in the gallery.
 */
import { readExif } from './exif';

/** Picture types the gallery shows, by file extension (zip entries and some Android files have no type). */
const EXTENSION_TYPES: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  jfif: 'image/jpeg',
  png: 'image/png',
  gif: 'image/gif',
  webp: 'image/webp',
  heic: 'image/heic',
  heif: 'image/heif',
};

const VIDEO = /\.(mp4|mov|m4v|3gp|webm|avi|mkv)$/i;

/** The picture type of a file, from its type or else its name; undefined for anything that is not a picture the gallery shows. */
export function pictureType(name: string, type?: string): string | undefined {
  const known = Object.values(EXTENSION_TYPES);
  if (type && known.includes(type)) return type;
  const ext = /\.([a-z0-9]+)$/i.exec(name)?.[1]?.toLowerCase();
  return ext ? EXTENSION_TYPES[ext] : undefined;
}

export function isVideoName(name: string): boolean {
  return VIDEO.test(name);
}

/**
 * A date written into a file or folder name, as phones and apps write them
 * (IMG_20190512_143000, PXL_20190512_143000123, 2019-05-12 14.30.00,
 * Screenshot_2019-05-12-14-30-00, IMG-20190512-WA0001, a folder named
 * 2019-05-12). Local time, in seconds; undefined when there is none.
 */
export function dateFromName(name: string): number | undefined {
  const full = /(?:^|[^0-9])((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?:[-_ T.]+([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d)\d{0,3})?(?![0-9])/.exec(name);
  if (!full) return undefined;
  const [y, mo, d] = [Number(full[1]), Number(full[2]), Number(full[3])];
  const [h, mi, s] = full[4] ? [Number(full[4]), Number(full[5]), Number(full[6])] : [12, 0, 0];
  const date = new Date(y, mo - 1, d, h, mi, s);
  if (date.getMonth() !== mo - 1 || date.getTime() > Date.now() + 86_400_000) return undefined;
  return Math.floor(date.getTime() / 1000);
}

export async function sha256Hex(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** Where `taken_at` came from, so the gallery can say how sure it is. */
export type TakenFrom = 'exif' | 'name' | 'file';

export interface PictureFacts {
  takenAt?: number;
  takenFrom?: TakenFrom;
  camera?: string;
  gps?: { lat: number; lon: number; alt?: number };
  width?: number;
  height?: number;
  orientation?: number;
}

/**
 * What can be learnt about a picture without changing it: EXIF first, then a
 * date in its name (or the folder it was in), then the file's own time.
 */
export function pictureFacts(bytes: Uint8Array, name: string, path?: string, fileTime?: number): PictureFacts {
  const exif = readExif(bytes);
  const facts: PictureFacts = {};
  const named = dateFromName(name) ?? (path ? dateFromName(path) : undefined);
  if (exif.takenAt) {
    facts.takenAt = exif.takenAt;
    facts.takenFrom = 'exif';
  } else if (named) {
    facts.takenAt = named;
    facts.takenFrom = 'name';
  } else if (fileTime && fileTime > 0) {
    facts.takenAt = Math.floor(fileTime);
    facts.takenFrom = 'file';
  }
  const camera = exif.model
    ? exif.make && !exif.model.toLowerCase().startsWith(exif.make.toLowerCase())
      ? `${exif.make} ${exif.model}`
      : exif.model
    : exif.make;
  if (camera) facts.camera = camera;
  if (exif.gps) facts.gps = exif.gps;
  if (exif.width && exif.height) {
    facts.width = exif.width;
    facts.height = exif.height;
  }
  if (exif.orientation) facts.orientation = exif.orientation;
  return facts;
}

/** One file to add: from the phone's picker or out of a zip. */
export interface ImportSource {
  name: string;
  /** The path inside a zip, when it came from one. */
  path?: string;
  size: number;
  /** The file's own time in seconds (lastModified, or the zip entry's time). */
  fileTime?: number;
  mime: string;
  read: () => Promise<Uint8Array<ArrayBuffer>>;
}

/** What is already in the gallery, for skipping duplicates. */
export interface Known {
  hashes: Set<string>;
  /** "name|taken_at" of pictures that have no hash (sent before hashes were kept). */
  named: Set<string>;
}

export function knownFrom(items: Array<{ sha256?: string; name?: string; takenAt?: number }>): Known {
  const known: Known = { hashes: new Set(), named: new Set() };
  for (const item of items) {
    if (item.sha256) known.hashes.add(item.sha256);
    if (item.name && item.takenAt) known.named.add(`${item.name}|${item.takenAt}`);
  }
  return known;
}

export function isKnown(known: Known, sha256: string, name: string, takenAt?: number): boolean {
  return known.hashes.has(sha256) || (takenAt !== undefined && known.named.has(`${name}|${takenAt}`));
}

/** The files of a picker selection, sorted into what can be added and what cannot. */
export function sortPicked(files: File[]): { pictures: ImportSource[]; zips: File[]; videos: string[]; other: string[] } {
  const out = { pictures: [] as ImportSource[], zips: [] as File[], videos: [] as string[], other: [] as string[] };
  for (const file of files) {
    const mime = pictureType(file.name, file.type);
    if (mime) {
      out.pictures.push({
        name: file.name,
        size: file.size,
        mime,
        ...(file.lastModified ? { fileTime: Math.floor(file.lastModified / 1000) } : {}),
        read: async () => new Uint8Array(await file.arrayBuffer()),
      });
    } else if (/\.zip$/i.test(file.name) || file.type === 'application/zip' || file.type === 'application/x-zip-compressed') {
      out.zips.push(file);
    } else if (isVideoName(file.name) || file.type.startsWith('video/')) {
      out.videos.push(file.name);
    } else {
      out.other.push(file.name);
    }
  }
  return out;
}

/** Hidden files and the debris other systems put in zips. */
export function isJunkPath(path: string): boolean {
  return path.split('/').some((part) => part.startsWith('.') || part === '__MACOSX' || part === 'Thumbs.db');
}

/**
 * Captions a backup may carry beside its pictures: a JSON file mapping a
 * picture's file name to its caption or text, as a list of objects or a plain
 * object. Anything that does not look like that gives an empty map; the
 * pictures are imported without captions.
 */
export function captionsFrom(text: string): Map<string, string> {
  const out = new Map<string, string>();
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return out;
  }
  const visit = (value: unknown, depth: number) => {
    if (depth > 6 || !value || typeof value !== 'object') return;
    if (Array.isArray(value)) {
      for (const v of value) visit(v, depth + 1);
      return;
    }
    const record = value as Record<string, unknown>;
    const file = ['file', 'filename', 'file_name', 'name', 'photo', 'image', 'path'].map((k) => record[k]).find((v) => typeof v === 'string');
    const caption = ['caption', 'text', 'message', 'content', 'comment', 'description'].map((k) => record[k]).find((v) => typeof v === 'string' && v.trim());
    if (typeof file === 'string' && typeof caption === 'string' && pictureType(file)) {
      out.set(file.split('/').pop() as string, caption.trim().slice(0, 2000));
    }
    for (const v of Object.values(record)) if (v && typeof v === 'object') visit(v, depth + 1);
  };
  visit(data, 0);
  return out;
}

/** The pictures (and any captions file) inside a zip, without reading the pictures yet. */
export async function zipSources(file: Blob): Promise<{ pictures: ImportSource[]; videos: string[]; captions: Map<string, string> }> {
  const { listZip, readZipEntry } = await import('./zipReader');
  const entries = (await listZip(file)).filter((e) => !isJunkPath(e.path));
  const captions = new Map<string, string>();
  for (const entry of entries) {
    if (/\.json$/i.test(entry.name) && entry.size > 0 && entry.size < 5 * 1024 * 1024 && !entry.encrypted) {
      try {
        for (const [k, v] of captionsFrom(new TextDecoder().decode(await readZipEntry(file, entry)))) captions.set(k, v);
      } catch {
        // Not a captions file: the pictures come in without captions.
      }
    }
  }
  const pictures: ImportSource[] = [];
  const videos: string[] = [];
  for (const entry of entries) {
    const mime = pictureType(entry.name);
    if (mime && !entry.encrypted) {
      pictures.push({
        name: entry.name,
        path: entry.path,
        size: entry.size,
        mime,
        ...(entry.modifiedAt ? { fileTime: entry.modifiedAt } : {}),
        read: () => readZipEntry(file, entry),
      });
    } else if (isVideoName(entry.name)) {
      videos.push(entry.path);
    }
  }
  return { pictures, videos, captions };
}

/** What the import needs from the chat: encryption and the server, passed in so it can be tested. */
export interface ImportDeps {
  encrypt: (plain: Uint8Array<ArrayBuffer>) => Promise<Uint8Array<ArrayBuffer>>;
  upload: (sealed: Uint8Array<ArrayBuffer>) => Promise<string>;
  seal: (payload: import('./privateChatCrypto').AddedPicture) => Promise<string>;
  addEntry: (sealed: string) => Promise<void>;
  deleteBlob: (blob: string) => Promise<void>;
}

export interface ImportProgress {
  done: number;
  total: number;
  added: number;
  skipped: number;
  failed: number;
  current?: string;
}

export interface ImportResult extends ImportProgress {
  failures: Array<{ name: string; reason: string }>;
  stopped: boolean;
}

/** The largest picture the chat keeps (the server's limit for one encrypted picture). */
export const MAX_PICTURE_BYTES = 25 * 1024 * 1024;

/**
 * Add pictures to the gallery one at a time: read, fingerprint, skip what is
 * already in (by the SHA-256 of the original bytes, or name and time for older
 * pictures), encrypt, upload, then record the sealed entry. Each picture is
 * complete before the next starts, so stopping or failing half way leaves
 * every picture either fully in or not in at all, and running it again picks
 * up where it stopped. The plaintext is overwritten with zeros once encrypted.
 */
export async function importPictures(
  sources: ImportSource[],
  known: Known,
  deps: ImportDeps,
  options: { source: 'phone' | 'between' | 'zip'; captions?: Map<string, string>; onProgress?: (p: ImportProgress) => void; shouldStop?: () => boolean; now?: () => number } = { source: 'phone' },
): Promise<ImportResult> {
  const now = options.now ?? (() => Math.floor(Date.now() / 1000));
  const result: ImportResult = { done: 0, total: sources.length, added: 0, skipped: 0, failed: 0, failures: [], stopped: false };
  const report = (current?: string) => options.onProgress?.({ done: result.done, total: result.total, added: result.added, skipped: result.skipped, failed: result.failed, ...(current ? { current } : {}) });
  report();
  for (const source of sources) {
    if (options.shouldStop?.()) {
      result.stopped = true;
      break;
    }
    report(source.name);
    let blob = '';
    try {
      if (source.size > MAX_PICTURE_BYTES) throw new Error('larger than 25 MB');
      const plain = await source.read();
      if (plain.length > MAX_PICTURE_BYTES) throw new Error('larger than 25 MB');
      const sha256 = await sha256Hex(plain);
      const facts = pictureFacts(plain, source.name, source.path, source.fileTime);
      if (isKnown(known, sha256, source.name, facts.takenAt)) {
        plain.fill(0);
        result.skipped += 1;
      } else {
        let sealed: Uint8Array<ArrayBuffer>;
        try {
          sealed = await deps.encrypt(plain);
        } finally {
          plain.fill(0);
        }
        blob = await deps.upload(sealed);
        const caption = options.captions?.get(source.name);
        const payload: import('./privateChatCrypto').AddedPicture = {
          t: 'added',
          blob,
          mime: source.mime,
          name: source.name,
          ...(source.path && source.path !== source.name ? { path: source.path } : {}),
          ...(caption ? { caption } : {}),
          ...(facts.takenAt ? { taken_at: facts.takenAt, taken_from: facts.takenFrom } : {}),
          added_at: now(),
          ...(source.fileTime ? { file_time: source.fileTime } : {}),
          sha256,
          size: plain.length || source.size,
          source: options.source,
          ...(facts.camera ? { camera: facts.camera } : {}),
          ...(facts.gps ? { gps: facts.gps } : {}),
          ...(facts.width && facts.height ? { width: facts.width, height: facts.height } : {}),
        };
        await deps.addEntry(await deps.seal(payload));
        blob = '';
        known.hashes.add(sha256);
        result.added += 1;
      }
    } catch (error) {
      if (blob) await deps.deleteBlob(blob).catch(() => {});
      result.failed += 1;
      result.failures.push({ name: source.name, reason: error instanceof Error && error.message ? error.message : 'could not be added' });
      // A locked chat cannot add anything more: stop rather than fail every picture left.
      if (error instanceof Error && /locked/i.test(error.message)) {
        result.done += 1;
        result.stopped = true;
        break;
      }
    }
    result.done += 1;
  }
  report();
  return result;
}
