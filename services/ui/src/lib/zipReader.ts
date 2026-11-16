/**
 * A small ZIP reader over a Blob (a picked file): it reads the central
 * directory from the end of the file, then each entry on demand, slicing only
 * that entry and inflating it with the browser's DecompressionStream. So a
 * backup of several gigabytes is never held in memory at once, which a phone
 * could not do. Stored and deflated entries are read (what every zip tool and
 * phone backup writes), ZIP64 included; encrypted entries are skipped.
 */

export interface ZipEntry {
  /** The path inside the zip, as written. */
  path: string;
  /** The file name alone. */
  name: string;
  size: number;
  compressedSize: number;
  method: number;
  /** The entry's modification time as the zip records it (local time, 2 s precision), in seconds. */
  modifiedAt?: number;
  encrypted: boolean;
  /** Where the entry's local header starts. */
  offset: number;
}

const EOCD = 0x06054b50;
const EOCD64 = 0x06064b50;
const EOCD64_LOCATOR = 0x07064b50;
const CENTRAL = 0x02014b50;
const LOCAL = 0x04034b50;

async function slice(blob: Blob, start: number, end: number): Promise<DataView> {
  return new DataView(await blob.slice(start, end).arrayBuffer());
}

function u64(view: DataView, at: number): number {
  return view.getUint32(at, true) + view.getUint32(at + 4, true) * 2 ** 32;
}

function dosTime(time: number, date: number): number | undefined {
  const y = 1980 + (date >> 9);
  const mo = (date >> 5) & 15;
  const d = date & 31;
  if (!mo || !d) return undefined;
  return Math.floor(new Date(y, mo - 1, d, time >> 11, (time >> 5) & 63, (time & 31) * 2).getTime() / 1000);
}

const utf8 = new TextDecoder();
const latin1 = new TextDecoder('latin1');

/** Every file in the zip (folders left out). Throws when the file is not a zip. */
export async function listZip(blob: Blob): Promise<ZipEntry[]> {
  const tailSize = Math.min(blob.size, 22 + 0xffff + 20);
  const tailStart = blob.size - tailSize;
  const tail = await slice(blob, tailStart, blob.size);
  let eocd = -1;
  for (let i = tail.byteLength - 22; i >= 0; i -= 1) {
    if (tail.getUint32(i, true) === EOCD) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new Error('This is not a zip file.');
  let count = tail.getUint16(eocd + 10, true);
  let dirSize = tail.getUint32(eocd + 12, true);
  let dirOffset = tail.getUint32(eocd + 16, true);
  if (eocd >= 20 && tail.getUint32(eocd - 20, true) === EOCD64_LOCATOR) {
    const at = u64(tail, eocd - 20 + 8);
    const record = await slice(blob, at, at + 56);
    if (record.getUint32(0, true) === EOCD64) {
      count = u64(record, 32);
      dirSize = u64(record, 40);
      dirOffset = u64(record, 48);
    }
  }
  const dir = await slice(blob, dirOffset, dirOffset + dirSize);
  const entries: ZipEntry[] = [];
  let at = 0;
  for (let i = 0; i < count && at + 46 <= dir.byteLength; i += 1) {
    if (dir.getUint32(at, true) !== CENTRAL) break;
    const flags = dir.getUint16(at + 8, true);
    const method = dir.getUint16(at + 10, true);
    const time = dir.getUint16(at + 12, true);
    const date = dir.getUint16(at + 14, true);
    let compressedSize = dir.getUint32(at + 20, true);
    let size = dir.getUint32(at + 24, true);
    const nameLength = dir.getUint16(at + 28, true);
    const extraLength = dir.getUint16(at + 30, true);
    const commentLength = dir.getUint16(at + 32, true);
    let offset = dir.getUint32(at + 42, true);
    const nameBytes = new Uint8Array(dir.buffer, at + 46, nameLength);
    const path = (flags & 0x800 ? utf8 : latin1).decode(nameBytes);
    // ZIP64: the real sizes and offset are in the extra field, in this order, for each that overflowed.
    let extra = at + 46 + nameLength;
    const extraEnd = extra + extraLength;
    while (extra + 4 <= extraEnd) {
      const id = dir.getUint16(extra, true);
      const len = dir.getUint16(extra + 2, true);
      if (id === 0x0001) {
        let p = extra + 4;
        if (size === 0xffffffff) {
          size = u64(dir, p);
          p += 8;
        }
        if (compressedSize === 0xffffffff) {
          compressedSize = u64(dir, p);
          p += 8;
        }
        if (offset === 0xffffffff) offset = u64(dir, p);
      }
      extra += 4 + len;
    }
    if (!path.endsWith('/')) {
      const modifiedAt = dosTime(time, date);
      entries.push({
        path,
        name: path.split('/').pop() || path,
        size,
        compressedSize,
        method,
        encrypted: Boolean(flags & 1),
        offset,
        ...(modifiedAt ? { modifiedAt } : {}),
      });
    }
    at = extraEnd + commentLength;
  }
  return entries;
}

/** One entry's bytes. Throws for an encrypted entry or a method other than stored or deflate. */
export async function readZipEntry(blob: Blob, entry: ZipEntry): Promise<Uint8Array<ArrayBuffer>> {
  if (entry.encrypted) throw new Error(`${entry.name} is password protected in the zip.`);
  const local = await slice(blob, entry.offset, entry.offset + 30);
  if (local.getUint32(0, true) !== LOCAL) throw new Error(`${entry.name} could not be found in the zip.`);
  const start = entry.offset + 30 + local.getUint16(26, true) + local.getUint16(28, true);
  const raw = blob.slice(start, start + entry.compressedSize);
  if (entry.method === 0) return new Uint8Array(await raw.arrayBuffer());
  if (entry.method !== 8) throw new Error(`${entry.name} uses a compression this app cannot read.`);
  const stream = raw.stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
