/**
 * A small EXIF reader for JPEG pictures: when the camera took it, which camera,
 * its orientation and where (GPS), read straight from the APP1 segment. No
 * dependency and no decoding of the picture itself; anything it cannot read
 * is simply left out. It reads only: the picture's bytes are never changed.
 */

export interface ExifInfo {
  /** When the camera took it, in seconds since the epoch (DateTimeOriginal, else DateTime). */
  takenAt?: number;
  /** Whether `takenAt` came with a time zone offset (OffsetTimeOriginal); otherwise it is read as this device's local time. */
  zoned?: boolean;
  make?: string;
  model?: string;
  /** 1 to 8, as EXIF numbers it. */
  orientation?: number;
  width?: number;
  height?: number;
  gps?: { lat: number; lon: number; alt?: number };
}

const TAG = {
  make: 0x010f,
  model: 0x0110,
  orientation: 0x0112,
  dateTime: 0x0132,
  exifIfd: 0x8769,
  gpsIfd: 0x8825,
  dateTimeOriginal: 0x9003,
  offsetTimeOriginal: 0x9011,
  pixelX: 0xa002,
  pixelY: 0xa003,
  gpsLatRef: 0x0001,
  gpsLat: 0x0002,
  gpsLonRef: 0x0003,
  gpsLon: 0x0004,
  gpsAltRef: 0x0005,
  gpsAlt: 0x0006,
} as const;

const TYPE_SIZE: Record<number, number> = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8 };

type Value = string | number[] | undefined;

class Tiff {
  private view: DataView;
  private start: number;
  private little: boolean;

  constructor(view: DataView, start: number, little: boolean) {
    this.view = view;
    this.start = start;
    this.little = little;
  }

  u16(at: number): number {
    return this.view.getUint16(this.start + at, this.little);
  }

  u32(at: number): number {
    return this.view.getUint32(this.start + at, this.little);
  }

  get length(): number {
    return this.view.byteLength - this.start;
  }

  /** One IFD's entries by tag; out-of-range offsets end the read rather than throwing. */
  ifd(offset: number): Map<number, Value> {
    const out = new Map<number, Value>();
    if (offset < 8 || offset + 2 > this.length) return out;
    const count = this.u16(offset);
    for (let i = 0; i < count; i += 1) {
      const entry = offset + 2 + i * 12;
      if (entry + 12 > this.length) break;
      const tag = this.u16(entry);
      const type = this.u16(entry + 2);
      const n = this.u32(entry + 4);
      const size = (TYPE_SIZE[type] ?? 0) * n;
      if (!size || size > 1 << 16) continue;
      const at = size <= 4 ? entry + 8 : this.u32(entry + 8);
      if (at + size > this.length) continue;
      out.set(tag, this.value(type, at, n));
    }
    return out;
  }

  private value(type: number, at: number, n: number): Value {
    if (type === 2) {
      let text = '';
      for (let i = 0; i < n; i += 1) {
        const c = this.view.getUint8(this.start + at + i);
        if (c === 0) break;
        text += String.fromCharCode(c);
      }
      return text.trim();
    }
    const values: number[] = [];
    for (let i = 0; i < Math.min(n, 16); i += 1) {
      if (type === 1 || type === 7) values.push(this.view.getUint8(this.start + at + i));
      else if (type === 3) values.push(this.u16(at + i * 2));
      else if (type === 4) values.push(this.u32(at + i * 4));
      else if (type === 9) values.push(this.view.getInt32(this.start + at + i * 4, this.little));
      else if (type === 5 || type === 10) {
        const signed = type === 10;
        const num = signed ? this.view.getInt32(this.start + at + i * 8, this.little) : this.u32(at + i * 8);
        const den = signed ? this.view.getInt32(this.start + at + i * 8 + 4, this.little) : this.u32(at + i * 8 + 4);
        values.push(den ? num / den : NaN);
      }
    }
    return values;
  }
}

/** "2019:05:12 14:30:00" (+ an optional "+09:00") to seconds since the epoch. */
export function exifDate(text: string | undefined, offset?: string): { at: number; zoned: boolean } | undefined {
  const m = /^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/.exec(text ?? '');
  if (!m) return undefined;
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number);
  if (y < 1900 || mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 60) return undefined;
  const zone = /^([+-])(\d{2}):?(\d{2})$/.exec(offset ?? '');
  if (zone) {
    const minutes = (Number(zone[2]) * 60 + Number(zone[3])) * (zone[1] === '-' ? -1 : 1);
    return { at: Math.floor(Date.UTC(y, mo - 1, d, h, mi, s) / 1000) - minutes * 60, zoned: true };
  }
  return { at: Math.floor(new Date(y, mo - 1, d, h, mi, s).getTime() / 1000), zoned: false };
}

function first(value: Value): number | undefined {
  return Array.isArray(value) && Number.isFinite(value[0]) ? value[0] : undefined;
}

function degrees(value: Value, ref: Value, negative: string): number | undefined {
  if (!Array.isArray(value) || value.length < 3 || value.some((v) => !Number.isFinite(v))) return undefined;
  const deg = value[0] + value[1] / 60 + value[2] / 3600;
  return typeof ref === 'string' && ref.toUpperCase().startsWith(negative) ? -deg : deg;
}

/** The APP1 "Exif" segment of a JPEG, as a TIFF reader; null when there is none. */
function findTiff(bytes: Uint8Array): Tiff | null {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length < 4 || view.getUint16(0) !== 0xffd8) return null;
  let at = 2;
  while (at + 4 <= bytes.length) {
    if (bytes[at] !== 0xff) return null;
    const marker = bytes[at + 1];
    if (marker === 0xd9 || marker === 0xda) return null; // end, or the picture data: no EXIF after this
    const size = view.getUint16(at + 2);
    if (marker === 0xe1 && size >= 16 && at + 2 + size <= bytes.length) {
      const id = String.fromCharCode(...bytes.subarray(at + 4, at + 10));
      if (id === 'Exif\0\0') {
        const start = at + 10;
        const order = view.getUint16(start);
        if (order !== 0x4949 && order !== 0x4d4d) return null;
        const little = order === 0x4949;
        const tiff = new Tiff(new DataView(bytes.buffer, bytes.byteOffset, Math.min(bytes.byteLength, at + 2 + size)), start, little);
        return tiff.u16(2) === 42 ? tiff : null;
      }
    }
    at += 2 + size;
  }
  return null;
}

/** What a JPEG's EXIF says; an empty object for anything else (or a JPEG without EXIF). */
export function readExif(bytes: Uint8Array): ExifInfo {
  let tiff: Tiff | null;
  try {
    tiff = findTiff(bytes);
  } catch {
    return {};
  }
  if (!tiff) return {};
  const info: ExifInfo = {};
  try {
    const main = tiff.ifd(tiff.u32(4));
    const exifAt = first(main.get(TAG.exifIfd));
    const exif = exifAt ? tiff.ifd(exifAt) : new Map<number, Value>();
    const original = exifDate(exif.get(TAG.dateTimeOriginal) as string | undefined, exif.get(TAG.offsetTimeOriginal) as string | undefined);
    const taken = original ?? exifDate(main.get(TAG.dateTime) as string | undefined);
    if (taken) {
      info.takenAt = taken.at;
      if (taken.zoned) info.zoned = true;
    }
    const make = main.get(TAG.make);
    const model = main.get(TAG.model);
    if (typeof make === 'string' && make) info.make = make;
    if (typeof model === 'string' && model) info.model = model;
    const orientation = first(main.get(TAG.orientation));
    if (orientation && orientation >= 1 && orientation <= 8) info.orientation = orientation;
    const width = first(exif.get(TAG.pixelX));
    const height = first(exif.get(TAG.pixelY));
    if (width && height) {
      info.width = width;
      info.height = height;
    }
    const gpsAt = first(main.get(TAG.gpsIfd));
    if (gpsAt) {
      const gps = tiff.ifd(gpsAt);
      const lat = degrees(gps.get(TAG.gpsLat), gps.get(TAG.gpsLatRef), 'S');
      const lon = degrees(gps.get(TAG.gpsLon), gps.get(TAG.gpsLonRef), 'W');
      // 0,0 is what a phone writes when it had no fix.
      if (lat !== undefined && lon !== undefined && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 && (lat !== 0 || lon !== 0)) {
        const altitude = first(gps.get(TAG.gpsAlt));
        const below = first(gps.get(TAG.gpsAltRef)) === 1;
        info.gps = { lat, lon, ...(altitude !== undefined ? { alt: below ? -altitude : altitude } : {}) };
      }
    }
  } catch {
    // A damaged EXIF block: keep what was read.
  }
  return info;
}
