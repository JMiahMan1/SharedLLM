import { describe, expect, it } from 'vitest';
import { CENTRED, MAX_ZOOM, clampCrop, cropRect, panned, zoomed } from './avatarCrop';

// A landscape photo, 1600 x 1000: its short side is the height.
const W = 1600;
const H = 1000;

describe('cropping a profile photo', () => {
  it('starts as the centre square of the short side', () => {
    expect(cropRect(CENTRED, W, H)).toEqual({ left: 300, top: 0, side: 1000 });
  });

  it('never lets the square hang off the photo, and keeps zoom in range', () => {
    expect(clampCrop({ x: 0, y: 0, zoom: 1 }, W, H)).toEqual({ x: 500 / 1600, y: 0.5, zoom: 1 });
    expect(clampCrop({ x: 1, y: 1, zoom: 2 }, W, H)).toEqual({ x: (1600 - 250) / 1600, y: 0.75, zoom: 2 });
    expect(clampCrop({ x: 0.5, y: 0.5, zoom: 99 }, W, H).zoom).toBe(MAX_ZOOM);
    expect(clampCrop({ x: Number.NaN, y: 0.5, zoom: 0 }, W, H)).toEqual(CENTRED);
  });

  it('moves the photo with the finger: dragging right shows more of the left', () => {
    // The view is 300 px across and shows 1000 photo pixels, so 30 px of drag is 100 photo pixels.
    const moved = panned(CENTRED, 30, 0, 300, W, H);
    expect(cropRect(moved, W, H).left).toBeCloseTo(200);
    // Up and down there is nothing more to see at zoom 1.
    expect(panned(CENTRED, 0, 50, 300, W, H).y).toBe(0.5);
  });

  it('zooms about the point under the fingers, which stays put', () => {
    const at = { fx: 0.25, fy: 0.25 };
    const before = cropRect(CENTRED, W, H);
    const point = { x: before.left + at.fx * before.side, y: before.top + at.fy * before.side };
    const after = cropRect(zoomed(CENTRED, 2, W, H, at.fx, at.fy), W, H);
    expect(after.side).toBe(500);
    expect(after.left + at.fx * after.side).toBeCloseTo(point.x);
    expect(after.top + at.fy * after.side).toBeCloseTo(point.y);
  });

  it('zooming back out lands inside the photo again', () => {
    const corner = clampCrop({ x: 0.9, y: 0.9, zoom: 4 }, W, H);
    const out = zoomed(corner, 1, W, H);
    const rect = cropRect(out, W, H);
    expect(rect.left + rect.side).toBeLessThanOrEqual(W);
    expect(rect.top).toBeGreaterThanOrEqual(0);
  });
});
