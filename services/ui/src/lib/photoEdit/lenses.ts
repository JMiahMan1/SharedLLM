import type { Point } from './recipe';

/**
 * Face lenses: playful things worn on a face (glasses, hats, ears, a bow),
 * drawn here as vector pictures and placed where a face was found.
 *
 * Everything in the catalogue is meant for the whole household, children
 * included: nothing frightening, nothing rude, nothing that mocks a person.
 * They are ordinary photo decorations, on the same footing as the stickers.
 *
 * A lens is drawn in a 200-wide box and knows what part of the face it sits
 * on. `span` says how wide the box is in eye distances (the distance between
 * the pupils, which stands in for how big the face is), and `origin` says
 * where the face point sits inside the box (a hat's brim is its bottom edge,
 * so its origin is 0.5, 1). That is all the arithmetic needed to fit one to a
 * face at any size, tilt or place in the picture.
 */

/** The fraction of the picture's width a lens covers when its scale is 1. */
export const LENS_SIZE = 0.4;

/** The drawing's own width; every lens is drawn inside a box this wide. */
export const LENS_VIEW_WIDTH = 200;

export type LensAnchor = 'eyes' | 'above' | 'nose';

export interface PhotoLens {
  id: string;
  label: string;
  /** Which face point the lens hangs on. */
  anchor: LensAnchor;
  /** The box's width, in eye distances. */
  span: number;
  /** The box's width divided by its height. */
  aspect: number;
  /** Where the face point sits in the box, as fractions (0.5, 0.5 is the middle). */
  origin: Point;
  /** For `above`: how far over the eyes the face point sits, in eye distances. */
  lift?: number;
  /** A complete set of SVG elements, drawn in a box `LENS_VIEW_WIDTH` wide. */
  svg: string;
}

/** A face as the lens arithmetic needs it: the two eyes, the nose, and the size. */
export interface FaceGeometry {
  /** The middle between the pupils, in the picture's pixels. */
  eyeMid: Point;
  /** The tip of the nose, in the picture's pixels. */
  nose: Point;
  /** The distance between the pupils, in pixels: the lens's ruler. */
  eyeDistance: number;
  /** The eye line's tilt, in degrees clockwise from level. */
  angle: number;
}

const EYES_ORIGIN: Point = [0.5, 0.5];
const ABOVE_ORIGIN: Point = [0.5, 1];

/**
 * Head-worn lenses are drawn upward from their bottom edge, which sits just
 * over the head: about one eye distance above the eyes is the top of the
 * head on most faces.
 */
const ABOVE_LIFT = 1.05;

export const LENSES: readonly PhotoLens[] = [
  {
    id: 'glasses',
    label: 'Glasses',
    anchor: 'eyes',
    span: 2.2,
    aspect: 2.5,
    origin: EYES_ORIGIN,
    svg: '<g fill="#bfdbfe" opacity="0.3"><circle cx="55" cy="40" r="27"/><circle cx="145" cy="40" r="27"/></g><g fill="none" stroke="#1f2937" stroke-width="7" stroke-linecap="round"><circle cx="55" cy="40" r="29"/><circle cx="145" cy="40" r="29"/><path d="M84 38 Q100 28 116 38"/><path d="M26 33 L6 25"/><path d="M174 33 L194 25"/></g><g fill="#fff" opacity="0.6"><path d="M38 24 L60 46 L53 52 L32 30Z"/><path d="M128 24 L150 46 L143 52 L122 30Z"/></g>',
  },
  {
    id: 'sunglasses',
    label: 'Shades',
    anchor: 'eyes',
    span: 2.2,
    aspect: 2.5,
    origin: EYES_ORIGIN,
    svg: '<path d="M18 20 H182 Q186 20 185 25 L178 50 Q175 62 162 62 H128 Q117 62 113 51 L108 36 H92 L87 51 Q83 62 72 62 H38 Q25 62 22 50 L15 25 Q14 20 18 20Z" fill="#111827"/><path d="M18 20 H182" stroke="#4b5563" stroke-width="7" stroke-linecap="round"/><g fill="#fff" opacity="0.3"><path d="M36 27 L58 56 L49 56 L32 32Z"/><path d="M126 27 L148 56 L139 56 L122 32Z"/></g>',
  },
  {
    id: 'heart-glasses',
    label: 'Heart glasses',
    anchor: 'eyes',
    span: 2.3,
    aspect: 2.4,
    origin: EYES_ORIGIN,
    svg: '<g fill="#f9a8d4" opacity="0.95" stroke="#db2777" stroke-width="5"><path d="M55 62 C36 48 28 38 28 28 C28 18 36 12 44 12 C50 12 54 16 55 20 C56 16 60 12 66 12 C74 12 82 18 82 28 C82 38 74 48 55 62Z"/><path d="M145 62 C126 48 118 38 118 28 C118 18 126 12 134 12 C140 12 144 16 145 20 C146 16 150 12 156 12 C164 12 172 18 172 28 C172 38 164 48 145 62Z"/></g><g fill="#fff" opacity="0.55"><path d="M38 22 L44 28 L40 33 L34 27Z"/><path d="M128 22 L134 28 L130 33 L124 27Z"/></g><path d="M82 36 Q100 26 118 36" fill="none" stroke="#db2777" stroke-width="5" stroke-linecap="round"/>',
  },
  {
    id: 'star-shades',
    label: 'Star shades',
    anchor: 'eyes',
    span: 2.3,
    aspect: 2.3,
    origin: EYES_ORIGIN,
    svg: '<g fill="#facc15" stroke="#b45309" stroke-width="5" stroke-linejoin="round"><path d="M55 8 L65 32 L91 34 L71 51 L77 76 L55 62 L33 76 L39 51 L19 34 L45 32Z"/><path d="M145 8 L155 32 L181 34 L161 51 L167 76 L145 62 L123 76 L129 51 L109 34 L135 32Z"/></g><path d="M79 40 L121 40" stroke="#b45309" stroke-width="6" stroke-linecap="round"/><g fill="#fff" opacity="0.6"><path d="M48 22 L56 32 L50 36 L43 27Z"/><path d="M138 22 L146 32 L140 36 L133 27Z"/></g>',
  },
  {
    id: 'party-hat',
    label: 'Party hat',
    anchor: 'above',
    span: 2.1,
    aspect: 1.5,
    origin: ABOVE_ORIGIN,
    lift: ABOVE_LIFT,
    svg: '<path d="M100 6 L136 132 H64Z" fill="#a855f7"/><path d="M72 106 H128 L124 120 H76Z" fill="#facc15"/><path d="M84 60 H116 L119 74 H81Z" fill="#facc15"/><path d="M100 6 L136 132 H118 L88 6Z" fill="#c084fc" opacity="0.55"/><circle cx="100" cy="10" r="12" fill="#fde68a"/><path d="M64 132 Q100 142 136 132" fill="none" stroke="#7e22ce" stroke-width="6"/>',
  },
  {
    id: 'crown',
    label: 'Crown',
    anchor: 'above',
    span: 2.2,
    aspect: 2.2,
    origin: ABOVE_ORIGIN,
    lift: ABOVE_LIFT,
    svg: '<path d="M28 96 L36 24 L70 56 L100 16 L130 56 L164 24 L172 96Z" fill="#fbbf24" stroke="#b45309" stroke-width="5" stroke-linejoin="round"/><path d="M28 96 H172 L168 116 H32Z" fill="#f59e0b" stroke="#b45309" stroke-width="5" stroke-linejoin="round"/><g><circle cx="100" cy="76" r="10" fill="#ef4444"/><circle cx="62" cy="82" r="8" fill="#3b82f6"/><circle cx="138" cy="82" r="8" fill="#22c55e"/></g>',
  },
  {
    id: 'bunny-ears',
    label: 'Bunny ears',
    anchor: 'above',
    span: 1.9,
    aspect: 1.4,
    origin: ABOVE_ORIGIN,
    lift: ABOVE_LIFT,
    svg: '<g stroke="#e5e7eb" stroke-width="6"><ellipse cx="62" cy="66" rx="24" ry="62" fill="#fff" transform="rotate(-12 62 66)"/><ellipse cx="138" cy="66" rx="24" ry="62" fill="#fff" transform="rotate(12 138 66)"/></g><g fill="#fbcfe8"><ellipse cx="62" cy="72" rx="12" ry="46" transform="rotate(-12 62 72)"/><ellipse cx="138" cy="72" rx="12" ry="46" transform="rotate(12 138 72)"/></g>',
  },
  {
    id: 'cat-ears',
    label: 'Cat ears',
    anchor: 'above',
    span: 2.2,
    aspect: 2.2,
    origin: ABOVE_ORIGIN,
    lift: ABOVE_LIFT,
    svg: '<path d="M32 96 L44 18 L96 62Z" fill="#1f2937"/><path d="M168 96 L156 18 L104 62Z" fill="#1f2937"/><path d="M48 84 L54 40 L84 66Z" fill="#f9a8d4"/><path d="M152 84 L146 40 L116 66Z" fill="#f9a8d4"/>',
  },
  {
    id: 'dog-ears',
    label: 'Puppy ears',
    anchor: 'above',
    span: 2.4,
    aspect: 1.6,
    origin: ABOVE_ORIGIN,
    lift: 0.95,
    svg: '<path d="M40 18 C10 26 2 70 12 104 C20 128 46 120 50 96 C54 72 56 40 56 24Z" fill="#a16207"/><path d="M160 18 C190 26 198 70 188 104 C180 128 154 120 150 96 C146 72 144 40 144 24Z" fill="#a16207"/><path d="M44 32 C22 44 18 76 24 98" fill="none" stroke="#fcd34d" stroke-width="6" stroke-linecap="round"/><path d="M156 32 C178 44 182 76 176 98" fill="none" stroke="#fcd34d" stroke-width="6" stroke-linecap="round"/>',
  },
  {
    id: 'flower-crown',
    label: 'Flower crown',
    anchor: 'above',
    span: 2.4,
    aspect: 2.6,
    origin: ABOVE_ORIGIN,
    lift: ABOVE_LIFT,
    svg: '<path d="M18 96 Q100 34 182 96" fill="none" stroke="#4d7c0f" stroke-width="7" stroke-linecap="round"/><g stroke="#fff" stroke-width="3"><g fill="#f9a8d4"><circle cx="34" cy="88" r="16"/><circle cx="72" cy="62" r="17"/><circle cx="130" cy="62" r="17"/><circle cx="168" cy="88" r="16"/><circle cx="100" cy="50" r="18"/></g></g><g fill="#facc15"><circle cx="34" cy="88" r="6"/><circle cx="72" cy="62" r="6"/><circle cx="130" cy="62" r="6"/><circle cx="168" cy="88" r="6"/><circle cx="100" cy="50" r="6"/></g>',
  },
  {
    id: 'halo',
    label: 'Halo',
    anchor: 'above',
    span: 2.3,
    aspect: 2.6,
    origin: [0.5, 0.5],
    lift: 1.7,
    svg: '<ellipse cx="100" cy="38" rx="76" ry="24" fill="none" stroke="#fde047" stroke-width="10"/><ellipse cx="100" cy="38" rx="76" ry="24" fill="none" stroke="#fff" stroke-width="3" opacity="0.7"/>',
  },
  {
    id: 'cowboy-hat',
    label: 'Cowboy hat',
    anchor: 'above',
    span: 2.6,
    aspect: 2.4,
    origin: ABOVE_ORIGIN,
    lift: ABOVE_LIFT,
    svg: '<ellipse cx="100" cy="104" rx="96" ry="22" fill="#92400e"/><path d="M52 104 C52 56 68 18 100 18 C132 18 148 56 148 104Z" fill="#a16207"/><path d="M52 96 H148 L146 112 H54Z" fill="#7c2d12"/><path d="M100 18 C118 40 126 70 128 104" fill="none" stroke="#d97706" stroke-width="6"/>',
  },
  {
    id: 'bow',
    label: 'Bow',
    anchor: 'above',
    span: 2.0,
    aspect: 2.4,
    origin: ABOVE_ORIGIN,
    lift: 1.0,
    svg: '<path d="M92 60 C66 24 22 22 18 46 C14 70 54 92 92 78Z" fill="#ec4899" stroke="#be185d" stroke-width="4"/><path d="M108 60 C134 24 178 22 182 46 C186 70 146 92 108 78Z" fill="#ec4899" stroke="#be185d" stroke-width="4"/><rect x="86" y="48" width="28" height="34" rx="10" fill="#db2777" stroke="#be185d" stroke-width="4"/><g fill="#fff" opacity="0.4"><path d="M40 40 L62 60 L52 66 L30 46Z"/><path d="M160 40 L138 60 L148 66 L170 46Z"/></g>',
  },
  {
    id: 'mustache',
    label: 'Mustache',
    anchor: 'nose',
    span: 1.7,
    aspect: 2.6,
    origin: [0.5, 0],
    svg: '<path d="M100 26 C88 8 62 4 44 14 C22 26 16 48 26 58 C36 68 52 58 58 44 C64 30 78 26 100 40 C122 26 136 30 142 44 C148 58 164 68 174 58 C184 48 178 26 156 14 C138 4 112 8 100 26Z" fill="#451a03"/>',
  },
  {
    id: 'clown-nose',
    label: 'Clown nose',
    anchor: 'nose',
    span: 0.62,
    aspect: 1,
    origin: EYES_ORIGIN,
    svg: '<circle cx="100" cy="100" r="92" fill="#ef4444"/><circle cx="72" cy="70" r="22" fill="#fff" opacity="0.55"/>',
  },
];

export function lensById(id: string): PhotoLens | null {
  return LENSES.find((l) => l.id === id) ?? null;
}

/** The lens as a standalone SVG document, ready for an <img> or a canvas. */
export function lensSvg(id: string): string | null {
  const lens = lensById(id);
  if (!lens) return null;
  const height = Math.round(LENS_VIEW_WIDTH / lens.aspect);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${LENS_VIEW_WIDTH} ${height}" width="${LENS_VIEW_WIDTH}" height="${height}">${lens.svg}</svg>`;
}

export function lensDataUrl(id: string): string | null {
  const svg = lensSvg(id);
  return svg ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` : null;
}

/**
 * Where a lens goes for a face: its point in the picture (fractions of the
 * width and the height), how big it is (a multiple of `LENS_SIZE`) and how
 * far it is turned, so it lies along the eye line.
 *
 * `picture` is the frame the face was found in: a lens's width is measured
 * against the picture's width and its height against the height, so both are
 * needed to put the point in the right place on a picture of any shape.
 */
export function lensPlacement(
  lens: PhotoLens,
  face: FaceGeometry,
  picture: { width: number; height: number },
): { x: number; y: number; scale: number; rotation: number } {
  const width = Math.max(1, picture.width);
  const height = Math.max(1, picture.height);
  const rad = (face.angle * Math.PI) / 180;
  // Up the face, in the picture's own axes (y grows downward).
  const up: Point = [Math.sin(rad), -Math.cos(rad)];
  const anchor: Point = lens.anchor === 'nose' ? face.nose : face.eyeMid;
  const lift = lens.anchor === 'above' ? (lens.lift ?? ABOVE_LIFT) * face.eyeDistance : 0;
  const boxWidth = lens.span * face.eyeDistance;
  return {
    x: (anchor[0] + up[0] * lift) / width,
    y: (anchor[1] + up[1] * lift) / height,
    scale: boxWidth / (LENS_SIZE * width),
    rotation: face.angle,
  };
}

/**
 * The middle of the two eyes and the eye line's tilt, from the two pupils.
 *
 * The angle is the smallest turn that lays a lens along the eye line, within
 * a quarter turn either way: which pupil a detector calls "right" depends on
 * whether the picture was taken in a mirror, and a lens must not end up
 * upside down because of that.
 */
export function eyesGeometry(rightEye: Point, leftEye: Point, nose?: Point): FaceGeometry {
  const eyeDistance = Math.hypot(leftEye[0] - rightEye[0], leftEye[1] - rightEye[1]);
  const eyeMid: Point = [(rightEye[0] + leftEye[0]) / 2, (rightEye[1] + leftEye[1]) / 2];
  let angle = (Math.atan2(leftEye[1] - rightEye[1], leftEye[0] - rightEye[0]) * 180) / Math.PI;
  if (angle > 90) angle -= 180;
  if (angle < -90) angle += 180;
  return {
    eyeMid,
    nose: nose ?? [eyeMid[0], eyeMid[1] + eyeDistance * 0.7],
    eyeDistance,
    angle,
  };
}
