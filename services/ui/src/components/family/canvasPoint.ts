export type Point = { x: number; y: number };

/** One stroke from (x1, y1) to (x2, y2), as the family board and note drawings paint it. */
export interface StrokeSegment {
  color: string;
  width: number;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export function paintSegment(context: CanvasRenderingContext2D, segment: StrokeSegment): void {
  context.strokeStyle = segment.color;
  context.lineWidth = segment.width;
  context.lineCap = 'round';
  context.lineJoin = 'round';
  context.beginPath();
  context.moveTo(segment.x1, segment.y1);
  context.lineTo(segment.x2, segment.y2);
  context.stroke();
}

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
