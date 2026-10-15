export type Point = { x: number; y: number };

/**
 * Where a pointer lands in the canvas's own pixel grid.
 *
 * The canvas draws on a fixed 640x420 grid but is shown at whatever width the
 * screen allows (about 350 px on a phone), so CSS pixels from the pointer
 * event must be scaled by grid size / displayed size. Without that, every
 * stroke landed at ~55% of the finger's distance from the top-left corner.
 */
export function canvasPoint(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  canvas: { width: number; height: number },
): Point {
  const sx = rect.width ? canvas.width / rect.width : 1;
  const sy = rect.height ? canvas.height / rect.height : 1;
  return { x: (clientX - rect.left) * sx, y: (clientY - rect.top) * sy };
}
