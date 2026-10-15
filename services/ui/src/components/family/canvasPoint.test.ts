import { describe, expect, it } from 'vitest';
import { canvasPoint } from './canvasPoint';

describe('canvasPoint', () => {
  it('scales a phone-width canvas up to its drawing grid', () => {
    // 640x420 grid shown 352x231 CSS px, 16 px from the left, 300 px down.
    const rect = { left: 16, top: 300, width: 352, height: 231 };
    const p = canvasPoint(16 + 176, 300 + 115.5, rect, { width: 640, height: 420 });
    expect(p.x).toBeCloseTo(320);
    expect(p.y).toBeCloseTo(210);
  });

  it('maps the corners to the corners', () => {
    const rect = { left: 10, top: 20, width: 320, height: 210 };
    expect(canvasPoint(10, 20, rect, { width: 640, height: 420 })).toEqual({ x: 0, y: 0 });
    expect(canvasPoint(330, 230, rect, { width: 640, height: 420 })).toEqual({ x: 640, y: 420 });
  });

  it('is 1:1 when shown at its own size, and safe before layout', () => {
    expect(canvasPoint(50, 60, { left: 0, top: 0, width: 640, height: 420 }, { width: 640, height: 420 })).toEqual({ x: 50, y: 60 });
    expect(canvasPoint(5, 6, { left: 0, top: 0, width: 0, height: 0 }, { width: 640, height: 420 })).toEqual({ x: 5, y: 6 });
  });
});
