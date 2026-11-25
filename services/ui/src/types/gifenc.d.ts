// gifenc 1.0.3 ships no types: the part of its API the photo editor uses.
declare module 'gifenc' {
  export type GifFormat = 'rgb565' | 'rgb444' | 'rgba4444';
  export type Palette = number[][];

  export interface QuantizeOptions {
    format?: GifFormat;
    oneBitAlpha?: boolean | number;
    clearAlpha?: boolean;
    clearAlphaThreshold?: number;
    clearAlphaColor?: number;
  }

  export function quantize(rgba: Uint8Array | Uint8ClampedArray, maxColors: number, options?: QuantizeOptions): Palette;
  export function applyPalette(rgba: Uint8Array | Uint8ClampedArray, palette: Palette, format?: GifFormat): Uint8Array;

  export interface FrameOptions {
    palette?: Palette;
    first?: boolean;
    transparent?: boolean;
    transparentIndex?: number;
    /** Milliseconds. */
    delay?: number;
    /** 0 loops forever, -1 plays once. */
    repeat?: number;
    dispose?: number;
  }

  export interface Encoder {
    writeFrame(index: Uint8Array, width: number, height: number, options?: FrameOptions): void;
    finish(): void;
    bytes(): Uint8Array;
    bytesView(): Uint8Array;
    reset(): void;
  }

  export function GIFEncoder(options?: { auto?: boolean; initialCapacity?: number }): Encoder;
  export default GIFEncoder;
}
