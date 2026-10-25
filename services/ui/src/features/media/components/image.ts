/**
 * Image sizing for the media surfaces (BUG-11, P7-T5): the gateway's image
 * proxy takes a `w=` (Music Assistant's `size=`), so cover art and list
 * thumbnails ask for the width they are actually drawn at, times the device
 * pixel ratio, instead of downloading a full-size cover.
 */

/** The device pixel ratio, capped so a 3x phone does not fetch 4x covers. */
export function imageDpr(): number {
  return typeof window === 'undefined' ? 1 : Math.min(2, Math.max(1, window.devicePixelRatio || 1));
}

/** The proxy `w=` for an image drawn at `cssPx` wide. */
export function imageWidth(cssPx: number): number {
  return Math.round(cssPx * imageDpr());
}
