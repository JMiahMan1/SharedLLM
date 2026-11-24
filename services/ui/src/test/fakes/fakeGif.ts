/**
 * Made-up animated pictures for tests: a real GIF of solid-colour frames (any
 * size), and the smallest WebP and PNG headers that say "animated" or not.
 * Written here byte by byte, so no picture from anywhere else is needed.
 */

type Rgb = [number, number, number];

/** LZW codes for a frame, never compressed: a clear code every two pixels keeps every code three bits wide. */
function frameData(pixels: number[]): number[] {
  const CLEAR = 4;
  const END = 5;
  const codes: number[] = [];
  for (let i = 0; i < pixels.length; i += 2) {
    codes.push(CLEAR, pixels[i]);
    if (i + 1 < pixels.length) codes.push(pixels[i + 1]);
  }
  codes.push(END);
  const bytes: number[] = [];
  let acc = 0;
  let bits = 0;
  for (const code of codes) {
    acc |= code << bits;
    bits += 3;
    while (bits >= 8) {
      bytes.push(acc & 0xff);
      acc >>= 8;
      bits -= 8;
    }
  }
  if (bits > 0) bytes.push(acc & 0xff);
  const out: number[] = [2];
  for (let i = 0; i < bytes.length; i += 255) {
    const chunk = bytes.slice(i, i + 255);
    out.push(chunk.length, ...chunk);
  }
  out.push(0);
  return out;
}

/**
 * A looping GIF, `width` x `height`, one solid colour per frame (up to four),
 * each shown for `delayCs` hundredths of a second. One colour is a still GIF.
 */
export function fakeGif(width: number, height: number, colours: Rgb[], delayCs = 20): Uint8Array<ArrayBuffer> {
  if (colours.length < 1 || colours.length > 4) throw new Error('one to four frames');
  const table: number[] = [];
  for (let i = 0; i < 4; i += 1) table.push(...(colours[i] ?? [0, 0, 0]));
  const le = (n: number) => [n & 0xff, (n >> 8) & 0xff];
  const out: number[] = [
    ...[...'GIF89a'].map((c) => c.charCodeAt(0)),
    ...le(width),
    ...le(height),
    0x81, // a global colour table of four
    0,
    0,
    ...table,
  ];
  if (colours.length > 1) {
    // NETSCAPE2.0: loop forever.
    out.push(0x21, 0xff, 0x0b, ...[...'NETSCAPE2.0'].map((c) => c.charCodeAt(0)), 3, 1, 0, 0, 0);
  }
  colours.forEach((_, index) => {
    out.push(0x21, 0xf9, 4, 0, ...le(delayCs), 0, 0);
    out.push(0x2c, 0, 0, 0, 0, ...le(width), ...le(height), 0);
    out.push(...frameData(new Array<number>(width * height).fill(index)));
  });
  out.push(0x3b);
  return new Uint8Array(out);
}

function chunk(type: string, payload: number[]): number[] {
  const size = payload.length;
  const out = [...type].map((c) => c.charCodeAt(0));
  out.push(size & 0xff, (size >> 8) & 0xff, (size >> 16) & 0xff, (size >> 24) & 0xff, ...payload);
  if (size & 1) out.push(0);
  return out;
}

/** A WebP container whose VP8X header does (or does not) set the animation flag. Header only: enough to be judged. */
export function fakeWebpHeader(animated: boolean): Uint8Array<ArrayBuffer> {
  const vp8x = chunk('VP8X', [animated ? 0x02 : 0x00, 0, 0, 0, 1, 0, 0, 1, 0, 0]);
  const body = [...[...'WEBP'].map((c) => c.charCodeAt(0)), ...vp8x, ...(animated ? chunk('ANIM', [0, 0, 0, 0, 0, 0]) : [])];
  const size = body.length;
  return new Uint8Array([...[...'RIFF'].map((c) => c.charCodeAt(0)), size & 0xff, (size >> 8) & 0xff, (size >> 16) & 0xff, size >> 24, ...body]);
}

/** A PNG signature and header, with an acTL chunk of `frames` frames when given. Header only. */
export function fakePngHeader(frames?: number): Uint8Array<ArrayBuffer> {
  const be = (n: number) => [(n >>> 24) & 0xff, (n >>> 16) & 0xff, (n >>> 8) & 0xff, n & 0xff];
  const pngChunk = (type: string, payload: number[]) => [...be(payload.length), ...[...type].map((c) => c.charCodeAt(0)), ...payload, 0, 0, 0, 0];
  return new Uint8Array([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
    ...pngChunk('IHDR', [...be(1), ...be(1), 8, 6, 0, 0, 0]),
    ...(frames ? pngChunk('acTL', [...be(frames), ...be(0)]) : []),
    ...pngChunk('IDAT', [0]),
    ...pngChunk('IEND', []),
  ]);
}
