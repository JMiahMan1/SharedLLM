/**
 * Tiny made-up JPEGs with synthetic EXIF, for tests: a real 2x2 picture with
 * an APP1 Exif block written here (no real camera, person or place).
 */

/** A 2x2 baseline JPEG (JFIF, made with an image library), without its leading SOI marker. */
const PIXEL_AFTER_SOI = Array.from(
  atob(
    '/+AAEEpGSUYAAQEAAAEAAQAA/9sAQwAQCwwODAoQDg0OEhEQExgoGhgWFhgxIyUdKDozPTw5Mzg3QEhcTkBEV0U3OFBtUVdfYmdoZz5NcXlwZHhcZWdj/9sAQwEREhIYFRgvGhovY0I4QmNjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2Nj/8AAEQgAAgACAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/aAAwDAQACEQMRAD8AjooooPbP/9k=',
  ),
  (c) => c.charCodeAt(0),
);

export interface FakeExif {
  dateTimeOriginal?: string;
  offsetTimeOriginal?: string;
  make?: string;
  model?: string;
  orientation?: number;
  gps?: { lat: number; lon: number; alt?: number };
  littleEndian?: boolean;
}

type Entry = { tag: number; type: number; count: number; data: number[] };

function ascii(tag: number, text: string): Entry {
  const data = [...text].map((c) => c.charCodeAt(0));
  data.push(0);
  return { tag, type: 2, count: data.length, data };
}

/** Build the TIFF block: IFD0, then the Exif IFD, then the GPS IFD, each followed by its out-of-line values. */
function tiff(exif: FakeExif): number[] {
  const le = Boolean(exif.littleEndian);
  const u16 = (v: number) => (le ? [v & 255, (v >> 8) & 255] : [(v >> 8) & 255, v & 255]);
  const u32 = (v: number) => (le ? [v & 255, (v >> 8) & 255, (v >> 16) & 255, (v >>> 24) & 255] : [(v >>> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255]);
  const rational = (values: Array<[number, number]>) => values.flatMap(([n, d]) => [...u32(n), ...u32(d)]);
  const dms = (deg: number): Array<[number, number]> => {
    const a = Math.abs(deg);
    const d = Math.floor(a);
    const m = Math.floor((a - d) * 60);
    const s = Math.round(((a - d) * 60 - m) * 60 * 100);
    return [[d, 1], [m, 1], [s, 100]];
  };

  const ifd0: Entry[] = [];
  if (exif.make) ifd0.push(ascii(0x010f, exif.make));
  if (exif.model) ifd0.push(ascii(0x0110, exif.model));
  if (exif.orientation) ifd0.push({ tag: 0x0112, type: 3, count: 1, data: [...u16(exif.orientation), 0, 0] });
  const exifIfd: Entry[] = [];
  if (exif.dateTimeOriginal) exifIfd.push(ascii(0x9003, exif.dateTimeOriginal));
  if (exif.offsetTimeOriginal) exifIfd.push(ascii(0x9011, exif.offsetTimeOriginal));
  const gpsIfd: Entry[] = [];
  if (exif.gps) {
    gpsIfd.push(ascii(0x0001, exif.gps.lat < 0 ? 'S' : 'N'));
    gpsIfd.push({ tag: 0x0002, type: 5, count: 3, data: rational(dms(exif.gps.lat)) });
    gpsIfd.push(ascii(0x0003, exif.gps.lon < 0 ? 'W' : 'E'));
    gpsIfd.push({ tag: 0x0004, type: 5, count: 3, data: rational(dms(exif.gps.lon)) });
    if (exif.gps.alt !== undefined) {
      gpsIfd.push({ tag: 0x0005, type: 1, count: 1, data: [exif.gps.alt < 0 ? 1 : 0, 0, 0, 0] });
      gpsIfd.push({ tag: 0x0006, type: 5, count: 1, data: rational([[Math.round(Math.abs(exif.gps.alt) * 10), 10]]) });
    }
  }
  // Pointers to the sub-IFDs are filled in once their offsets are known.
  const exifPointer: Entry = { tag: 0x8769, type: 4, count: 1, data: [0, 0, 0, 0] };
  const gpsPointer: Entry = { tag: 0x8825, type: 4, count: 1, data: [0, 0, 0, 0] };
  if (exifIfd.length) ifd0.push(exifPointer);
  if (gpsIfd.length) ifd0.push(gpsPointer);

  const size = (entries: Entry[]) => 2 + entries.length * 12 + 4 + entries.reduce((n, e) => n + (e.data.length > 4 ? e.data.length : 0), 0);
  const ifd0At = 8;
  const exifAt = ifd0At + size(ifd0);
  const gpsAt = exifAt + (exifIfd.length ? size(exifIfd) : 0);
  exifPointer.data = u32(exifAt);
  gpsPointer.data = u32(gpsAt);

  const write = (entries: Entry[], at: number): number[] => {
    const sorted = [...entries].sort((a, b) => a.tag - b.tag);
    let extra = at + 2 + sorted.length * 12 + 4;
    const head: number[] = [...u16(sorted.length)];
    const tail: number[] = [];
    for (const e of sorted) {
      head.push(...u16(e.tag), ...u16(e.type), ...u32(e.count));
      if (e.data.length > 4) {
        head.push(...u32(extra));
        tail.push(...e.data);
        extra += e.data.length;
      } else {
        head.push(...e.data, ...new Array<number>(4 - e.data.length).fill(0));
      }
    }
    return [...head, 0, 0, 0, 0, ...tail];
  };
  return [
    ...(le ? [0x49, 0x49] : [0x4d, 0x4d]),
    ...u16(42),
    ...u32(ifd0At),
    ...write(ifd0, ifd0At),
    ...(exifIfd.length ? write(exifIfd, exifAt) : []),
    ...(gpsIfd.length ? write(gpsIfd, gpsAt) : []),
  ];
}

/** A 2x2 JPEG carrying the given EXIF (or none). A different `salt` makes different bytes for the same EXIF. */
export function fakeJpeg(exif?: FakeExif, salt = 0): Uint8Array<ArrayBuffer> {
  const out: number[] = [0xff, 0xd8];
  if (exif) {
    const body = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00, ...tiff(exif)];
    out.push(0xff, 0xe1, ((body.length + 2) >> 8) & 255, (body.length + 2) & 255, ...body);
  }
  if (salt) out.push(0xff, 0xfe, 0x00, 0x06, (salt >>> 24) & 255, (salt >> 16) & 255, (salt >> 8) & 255, salt & 255);
  out.push(...PIXEL_AFTER_SOI);
  return new Uint8Array(out);
}
