import { eyesGeometry, type FaceGeometry } from './lenses';
import type { Frame } from './geometry';
import type { Point } from './recipe';

/**
 * Finding faces in a picture, on the phone, with nothing sent anywhere.
 *
 * The detector is MediaPipe's small BlazeFace model (about a quarter of a
 * megabyte) running on the phone's own GPU or CPU. The model and the WebAssembly
 * code that runs it are served from this app's own address (see
 * `scripts/copy-lens-assets.mjs`), so a phone that has the app has the
 * detector: no model is fetched from anyone else, and the picture never leaves
 * the page.
 *
 * It is loaded the first time a lens is put on a picture, not before: the
 * editor opens and works without it, and a phone where the detector cannot
 * start still gets lenses it can drag onto a face by hand.
 */

/** A face, ready for the lens arithmetic, in the picture's own pixels. */
export interface FoundFace extends FaceGeometry {
  /** The detector's box round the whole face, in pixels. */
  box: { x: number; y: number; width: number; height: number };
}

/** A detection, as much of it as this app reads. */
interface Detection {
  boundingBox: { originX: number; originY: number; width: number; height: number };
  keypoints?: ReadonlyArray<{ x: number; y: number }>;
}

/** The part of the detector this app uses, so a test can stand in for it. */
export interface FaceDetectorLike {
  detect(image: HTMLCanvasElement): { detections: ReadonlyArray<Detection> };
}

/** What went wrong, in words someone using the phone can act on. */
export class FaceFindError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'FaceFindError';
  }
}

/** Where the detector's files sit, on whatever path the app itself is served from. */
function lensAssetsBase(): string {
  const base = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');
  return `${base}/lens/`;
}

let detectorPromise: Promise<FaceDetectorLike> | null = null;

/**
 * The detector, made once and kept. If the phone's GPU cannot run it, the same
 * model is tried on the CPU before giving up.
 */
async function loadDetector(): Promise<FaceDetectorLike> {
  if (!detectorPromise) {
    detectorPromise = (async () => {
      let tasks: typeof import('@mediapipe/tasks-vision');
      try {
        tasks = await import('@mediapipe/tasks-vision');
      } catch {
        throw new FaceFindError('Face finding is not available on this phone. Place the lens yourself and drag it onto the face.');
      }
      const fileset = {
        wasmLoaderPath: `${lensAssetsBase()}vision_wasm_internal.js`,
        wasmBinaryPath: `${lensAssetsBase()}vision_wasm_internal.wasm`,
      };
      const options = (delegate: 'GPU' | 'CPU') => ({
        baseOptions: { modelAssetPath: `${lensAssetsBase()}blaze_face_short_range.tflite`, delegate },
        runningMode: 'IMAGE' as const,
        minDetectionConfidence: 0.4,
      });
      try {
        return (await tasks.FaceDetector.createFromOptions(fileset, options('GPU'))) as unknown as FaceDetectorLike;
      } catch {
        try {
          return (await tasks.FaceDetector.createFromOptions(fileset, options('CPU'))) as unknown as FaceDetectorLike;
        } catch {
          throw new FaceFindError('Face finding could not start on this phone. Place the lens yourself and drag it onto the face.');
        }
      }
    })();
    // A failed start is not remembered: the next tap tries again.
    detectorPromise.catch(() => {
      detectorPromise = null;
    });
  }
  return detectorPromise;
}

/** The frame as an ordinary canvas, the one kind of picture the detector reads. */
function toCanvas(frame: Frame): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = frame.width;
  canvas.height = frame.height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new FaceFindError('The picture could not be read for face finding on this phone.');
  ctx.putImageData(new ImageData(new Uint8ClampedArray(frame.data), frame.width, frame.height), 0, 0);
  return canvas;
}

/**
 * A detection as a face the lenses can use. The six points are, in the
 * detector's order, the right eye, the left eye, the nose tip, the mouth and
 * the two ears; when it gives no points, the box alone places the eyes a
 * third of the way down and a third of its width apart, which is close enough
 * for a lens someone can nudge.
 */
export function faceFromDetection(d: Detection): FoundFace {
  const b = d.boundingBox;
  const box = { x: b.originX, y: b.originY, width: b.width, height: b.height };
  const points = d.keypoints ?? [];
  const at = (i: number, fallback: Point): Point => {
    const p = points[i];
    return p && Number.isFinite(p.x) && Number.isFinite(p.y) ? [p.x, p.y] : fallback;
  };
  const midX = b.originX + b.width / 2;
  const rightEye = at(0, [midX - b.width * 0.16, b.originY + b.height * 0.36]);
  const leftEye = at(1, [midX + b.width * 0.16, b.originY + b.height * 0.36]);
  const nose = at(2, [midX, b.originY + b.height * 0.58]);
  const geometry = eyesGeometry(rightEye, leftEye, nose);
  // With no eye points at all the distance between them is made up; the
  // face's own width is the better ruler then.
  const eyeDistance = points.length >= 2 && geometry.eyeDistance > 0 ? geometry.eyeDistance : b.width * 0.34;
  return { ...geometry, eyeDistance, box };
}

/** Every face in the picture, the biggest first. */
export async function findFaces(frame: Frame, detector?: FaceDetectorLike): Promise<FoundFace[]> {
  if (frame.width < 8 || frame.height < 8) return [];
  const d = detector ?? (await loadDetector());
  const canvas = toCanvas(frame);
  try {
    const result = d.detect(canvas);
    return (result.detections ?? [])
      .map((x) => faceFromDetection(x))
      .filter((f) => f.eyeDistance > 1 && f.box.width > 4)
      .sort((a, b) => b.box.width * b.box.height - a.box.width * a.box.height);
  } catch (e) {
    if (e instanceof FaceFindError) throw e;
    throw new FaceFindError('Face finding did not finish on this phone. Place the lens yourself and drag it onto the face.');
  } finally {
    canvas.width = 0;
    canvas.height = 0;
  }
}
