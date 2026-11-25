import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Frame } from './geometry';

/**
 * jsdom has no canvas, and the detector needs one to read a picture. A stand-in
 * that keeps the pixels and reports them back is enough for the arithmetic
 * under test; the real detector never runs here.
 */
function stubCanvas() {
  // jsdom has neither a canvas nor ImageData; both are ordinary browser furniture.
  if (typeof globalThis.ImageData === 'undefined') {
    Object.defineProperty(globalThis, 'ImageData', {
      configurable: true,
      writable: true,
      value: class ImageDataStub {
        data: Uint8ClampedArray;
        constructor(data: Uint8ClampedArray) {
          this.data = data;
        }
      },
    });
  }
  const put = vi.fn();
  const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    return { putImageData: put, getImageData: () => ({ data: new Uint8ClampedArray(4) }) } as unknown as CanvasRenderingContext2D;
  } as never);
  return { getContext, put };
}

function frame(width = 40, height = 30): Frame {
  return { width, height, data: new Uint8ClampedArray(width * height * 4) };
}

const face = (originX: number, originY: number, width: number, keypoints?: Array<{ x: number; y: number }>) => ({
  boundingBox: { originX, originY, width, height: width * 1.3 },
  keypoints,
});

describe('reading the detector’s answer', () => {
  it('takes the eyes, the nose and the face’s box from the six points', async () => {
    stubCanvas();
    const { faceFromDetection } = await import('./faceDetect');
    const f = faceFromDetection(
      face(100, 50, 80, [
        { x: 115, y: 82 },
        { x: 155, y: 82 },
        { x: 135, y: 105 },
        { x: 135, y: 118 },
        { x: 106, y: 92 },
        { x: 164, y: 92 },
      ]),
    );
    expect(f.eyeMid).toEqual([135, 82]);
    expect(f.nose).toEqual([135, 105]);
    expect(f.eyeDistance).toBeCloseTo(40);
    expect(f.angle).toBeCloseTo(0);
    expect(f.box).toEqual({ x: 100, y: 50, width: 80, height: 104 });
  });

  it('places the eyes from the box alone when the detector gives no points', async () => {
    stubCanvas();
    const { faceFromDetection } = await import('./faceDetect');
    const f = faceFromDetection(face(100, 50, 80));
    expect(f.eyeMid[0]).toBeCloseTo(140);
    expect(f.eyeMid[1]).toBeCloseTo(50 + 104 * 0.36);
    expect(f.eyeDistance).toBeCloseTo(80 * 0.34);
    expect(f.angle).toBeCloseTo(0);
  });
});

describe('finding faces in a picture', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns the biggest face first and keeps the geometry in the picture’s pixels', async () => {
    stubCanvas();
    const { findFaces } = await import('./faceDetect');
    const detector = {
      detect: vi.fn(() => ({
        detections: [
          face(10, 10, 40, [
            { x: 20, y: 20 },
            { x: 36, y: 20 },
            { x: 28, y: 32 },
          ]),
          face(60, 10, 120, [
            { x: 90, y: 40 },
            { x: 138, y: 40 },
            { x: 114, y: 70 },
          ]),
        ],
      })),
    };
    const faces = await findFaces(frame(), detector);
    expect(detector.detect).toHaveBeenCalledOnce();
    expect(faces.map((f) => f.box.width)).toEqual([120, 40]);
    expect(faces[0].eyeDistance).toBeCloseTo(48);
    expect(faces[0].eyeMid).toEqual([114, 40]);
  });

  it('finds nothing in a picture too small to hold a face', async () => {
    const detect = vi.fn();
    const { findFaces } = await import('./faceDetect');
    expect(await findFaces({ width: 4, height: 4, data: new Uint8ClampedArray(64) }, { detect })).toEqual([]);
    expect(detect).not.toHaveBeenCalled();
  });

  it('says so in words when the detector itself fails, and points at placing a lens by hand', async () => {
    stubCanvas();
    const { findFaces, FaceFindError } = await import('./faceDetect');
    const detector = {
      detect: () => {
        throw new Error('wasm exploded');
      },
    };
    await expect(findFaces(frame(), detector)).rejects.toBeInstanceOf(FaceFindError);
    await expect(findFaces(frame(), detector)).rejects.toThrow(/drag it onto the face/);
  });
});
