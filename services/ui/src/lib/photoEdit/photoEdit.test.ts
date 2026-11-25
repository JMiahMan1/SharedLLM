import { describe, expect, it } from 'vitest';
import { applyRecipe, composite, outputSize, type EditFrame } from './apply';
import { animatedFrameSize, frameMs, looksAnimated, MAX_FRAMES, MAX_TOTAL_PIXELS } from './decode';
import { applyLook, buildLut, FILTERS, grainAt, isNeutral, lookFor, NEUTRAL_LOOK } from './filters';
import { cropFrame, downscale, fitCrop, fitScale, makeFrame, moveCrop, normaliseCrop, orient, resizeCrop, MIN_CROP } from './geometry';
import { addStroke, contrastOn, drawingOf, EMOJI, pinchChange, removeOverlay, STICKERS, stickerSvg, thinPoints, undoStroke } from './overlays';
import { emptyRecipe, FILTER_IDS, isIdentity, MAX_OVERLAYS, MAX_POINTS, NO_ADJUSTMENTS, parseRecipe, type EditRecipe } from './recipe';
import { fakeGif, fakePngHeader, fakeWebpHeader } from '../../test/fakes/fakeGif';

/** A frame where pixel (x, y) has red x, green y, blue 7, so where each pixel went can be read back. */
function grid(width: number, height: number, duration = 0): EditFrame {
  const f = makeFrame(width, height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4;
      f.data.set([x, y, 7, 255], i);
    }
  }
  return { ...f, duration };
}

function solid(width: number, height: number, rgba: [number, number, number, number], duration = 0): EditFrame {
  const f = makeFrame(width, height);
  for (let i = 0; i < f.data.length; i += 4) f.data.set(rgba, i);
  return { ...f, duration };
}

const px = (f: { width: number; data: Uint8ClampedArray }, x: number, y: number) => Array.from(f.data.slice((y * f.width + x) * 4, (y * f.width + x) * 4 + 4));

describe('the recipe', () => {
  it('starts as "nothing changed" and says so', () => {
    expect(isIdentity(emptyRecipe())).toBe(true);
    expect(isIdentity({ ...emptyRecipe(), filter: 'noir' })).toBe(false);
    expect(isIdentity({ ...emptyRecipe(), adjust: { ...NO_ADJUSTMENTS, warmth: 5 } })).toBe(false);
    expect(isIdentity({ ...emptyRecipe(), rotate: 90 })).toBe(false);
    expect(isIdentity({ ...emptyRecipe(), crop: { x: 0, y: 0, w: 0.5, h: 0.5 } })).toBe(false);
    // An empty drawing layer (every line undone) changes nothing.
    expect(isIdentity({ ...emptyRecipe(), overlays: [{ id: 'drawing', kind: 'drawing', strokes: [] }] })).toBe(true);
  });

  it('survives a trip through JSON exactly', () => {
    const recipe: EditRecipe = {
      ...emptyRecipe(),
      filter: 'vintage',
      adjust: { brightness: 10, contrast: -20, saturation: 30, warmth: -40, vignette: 50 },
      rotate: 270,
      flipH: true,
      aspect: 'square',
      crop: { x: 0.1, y: 0.2, w: 0.5, h: 0.5 },
      overlays: [
        { id: 'drawing', kind: 'drawing', strokes: [{ color: '#ff0000', width: 0.02, points: [[0.1, 0.1], [0.2, 0.3]] }] },
        { id: 't1', kind: 'text', text: 'Hello\nthere', font: 'serif', color: '#facc15', pill: true, x: 0.4, y: 0.6, scale: 1.5, rotation: -12 },
        { id: 'e1', kind: 'emoji', emoji: '🌈', x: 0.2, y: 0.2, scale: 1, rotation: 0 },
        { id: 's1', kind: 'sticker', sticker: 'heart', x: 0.8, y: 0.8, scale: 0.5, rotation: 45 },
      ],
    };
    expect(parseRecipe(JSON.stringify(recipe))).toEqual(recipe);
  });

  it('refuses what is not a recipe and tames what is out of range', () => {
    expect(parseRecipe('not json')).toBeNull();
    expect(parseRecipe({ v: 99 })).toBeNull();
    expect(parseRecipe(null)).toBeNull();
    const parsed = parseRecipe({
      v: 1,
      filter: 'sparkly-unknown',
      adjust: { brightness: 900, contrast: 'loud', vignette: -5 },
      rotate: 45,
      crop: { x: 0.9, y: 0, w: 0.5, h: 2 },
      overlays: [
        { kind: 'text', text: '   ' },
        { kind: 'text', text: 'x'.repeat(500), color: 'red', font: 'comic' },
        { kind: 'sticker', sticker: '../../etc' },
        { kind: 'script', src: 'evil' },
      ],
    })!;
    expect(parsed.filter).toBeNull();
    expect(parsed.adjust).toEqual({ brightness: 100, contrast: 0, saturation: 0, warmth: 0, vignette: 0 });
    expect(parsed.rotate).toBe(0);
    expect(parsed.crop).toEqual({ x: 0.9, y: 0, w: expect.closeTo(0.1, 6), h: 1 });
    expect(parsed.overlays).toHaveLength(1);
    const text = parsed.overlays[0];
    expect(text.kind === 'text' && text.text.length).toBe(200);
    expect(text.kind === 'text' && [text.color, text.font]).toEqual(['#ffffff', 'sans']);
  });

  it('caps how much a stored recipe can carry', () => {
    const many = Array.from({ length: MAX_OVERLAYS + 20 }, (_, i) => ({ kind: 'emoji', emoji: '⭐', id: `e${i}` }));
    expect(parseRecipe({ v: 1, overlays: many })!.overlays).toHaveLength(MAX_OVERLAYS);
    const points = Array.from({ length: MAX_POINTS + 500 }, () => [0.5, 0.5]);
    const drawing = parseRecipe({ v: 1, overlays: [{ kind: 'drawing', strokes: [{ color: '#000000', width: 0.01, points }] }] })!.overlays[0];
    expect(drawing.kind === 'drawing' && drawing.strokes[0].points.length).toBe(MAX_POINTS);
  });
});

describe('filters and adjustments', () => {
  it('has twelve one-tap looks, each a known id with a label', () => {
    expect(FILTERS).toHaveLength(12);
    expect(FILTERS.map((f) => f.id)).toEqual([...FILTER_IDS]);
    for (const f of FILTERS) expect(f.label).toMatch(/^[A-Z][a-z]+$/);
  });

  it('leaves the pixels alone with no filter and no adjustment', () => {
    const look = lookFor(null, NO_ADJUSTMENTS);
    expect(isNeutral(look)).toBe(true);
    const f = grid(4, 3);
    const before = new Uint8ClampedArray(f.data);
    applyLook(f.data, f.width, f.height, look);
    expect(f.data).toEqual(before);
    expect(Array.from(buildLut(NEUTRAL_LOOK).slice(0, 256))).toEqual(Array.from({ length: 256 }, (_, i) => i));
  });

  it('every filter changes a colourful picture, and keeps alpha', () => {
    for (const filter of FILTERS) {
      const f = solid(8, 8, [180, 90, 60, 200]);
      applyLook(f.data, 8, 8, lookFor(filter.id, NO_ADJUSTMENTS));
      expect(px(f, 4, 4).slice(0, 3), filter.id).not.toEqual([180, 90, 60]);
      expect(px(f, 4, 4)[3]).toBe(200);
    }
  });

  it('mono and noir make grey', () => {
    for (const id of ['mono', 'noir'] as const) {
      const f = solid(10, 10, [200, 40, 90, 255]);
      applyLook(f.data, 10, 10, { ...lookFor(id, NO_ADJUSTMENTS), grain: 0, vignette: 0 });
      const [r, g, b] = px(f, 5, 5);
      expect(Math.abs(r - g)).toBeLessThanOrEqual(1);
      expect(Math.abs(g - b)).toBeLessThanOrEqual(1);
    }
  });

  it('warm adds orange, cool adds blue', () => {
    const grey = () => solid(4, 4, [128, 128, 128, 255]);
    const warm = grey();
    applyLook(warm.data, 4, 4, lookFor('warm', NO_ADJUSTMENTS));
    expect(px(warm, 1, 1)[0]).toBeGreaterThan(px(warm, 1, 1)[2]);
    const cool = grey();
    applyLook(cool.data, 4, 4, lookFor('cool', NO_ADJUSTMENTS));
    expect(px(cool, 1, 1)[2]).toBeGreaterThan(px(cool, 1, 1)[0]);
  });

  it('the sliders go the way their names say', () => {
    const run = (adjust: Partial<typeof NO_ADJUSTMENTS>) => {
      const f = solid(9, 9, [100, 120, 140, 255]);
      applyLook(f.data, 9, 9, lookFor(null, { ...NO_ADJUSTMENTS, ...adjust }));
      return f;
    };
    expect(px(run({ brightness: 50 }), 4, 4)[0]).toBeGreaterThan(100);
    expect(px(run({ brightness: -50 }), 4, 4)[0]).toBeLessThan(100);
    const sat = px(run({ saturation: -100 }), 4, 4);
    expect(sat[0]).toBe(sat[2]);
    // The vignette darkens the corners, not the middle.
    const v = run({ vignette: 100 });
    expect(px(v, 0, 0)[0]).toBeLessThan(px(v, 4, 4)[0]);
    expect(px(v, 4, 4)[0]).toBe(100);
  });

  it('grain is the same every time for the same place (so every frame matches)', () => {
    expect(grainAt(10, 20)).toBe(grainAt(10, 20));
    expect(grainAt(10, 20)).not.toBe(grainAt(11, 20));
    const values = Array.from({ length: 1000 }, (_, i) => grainAt(i, i * 3));
    expect(Math.min(...values)).toBeGreaterThanOrEqual(-1);
    expect(Math.max(...values)).toBeLessThanOrEqual(1);
  });
});

describe('turning, flipping, cropping, shrinking', () => {
  it('turns clockwise', () => {
    const f = grid(3, 2);
    const r = orient(f, 90, false, false);
    expect([r.width, r.height]).toEqual([2, 3]);
    // The bottom-left of the source is now the top-left.
    expect(px(r, 0, 0).slice(0, 2)).toEqual([0, 1]);
    expect(px(r, 1, 0).slice(0, 2)).toEqual([0, 0]);
    const back = orient(orient(orient(r, 90, false, false), 90, false, false), 90, false, false);
    expect(back.data).toEqual(f.data);
    expect(orient(f, 180, false, false).data).toEqual(orient(orient(f, 90, false, false), 90, false, false).data);
    expect(orient(f, 270, false, false).data).toEqual(orient(orient(f, 180, false, false), 90, false, false).data);
  });

  it('flips, and gives back the same frame when nothing changes', () => {
    const f = grid(3, 2);
    expect(orient(f, 0, false, false)).toBe(f);
    expect(px(orient(f, 0, true, false), 0, 0).slice(0, 2)).toEqual([2, 0]);
    expect(px(orient(f, 0, false, true), 0, 0).slice(0, 2)).toEqual([0, 1]);
  });

  it('crops to whole pixels inside the picture', () => {
    const c = cropFrame(grid(10, 10), { x: 0.2, y: 0.3, w: 0.5, h: 0.4 });
    expect([c.width, c.height]).toEqual([5, 4]);
    expect(px(c, 0, 0).slice(0, 2)).toEqual([2, 3]);
  });

  it('shrinks by averaging', () => {
    const f = makeFrame(2, 2);
    f.data.set([0, 0, 0, 255, 255, 255, 255, 255, 255, 255, 255, 255, 0, 0, 0, 255]);
    const d = downscale(f, 1, 1);
    expect(px(d, 0, 0)).toEqual([128, 128, 128, 255]);
    expect(fitScale(4000, 3000, 2000, 1e9)).toBeCloseTo(0.5);
    expect(fitScale(4000, 4000, 1e9, 4_000_000)).toBeCloseTo(0.5);
    expect(fitScale(10, 10, 100, 100000)).toBe(1);
  });

  it('fits a crop shape in the middle of the picture', () => {
    expect(fitCrop(null, 400, 300)).toEqual({ x: 0, y: 0, w: 1, h: 1 });
    const square = fitCrop(1, 400, 300);
    expect(square.w * 400).toBeCloseTo(square.h * 300);
    expect(square.h).toBe(1);
    expect(square.x).toBeCloseTo((1 - square.w) / 2);
    const wide = fitCrop(16 / 9, 300, 400);
    expect((wide.w * 300) / (wide.h * 400)).toBeCloseTo(16 / 9);
    expect(normaliseCrop({ x: 0, y: 0, w: 1, h: 1 })).toBeNull();
  });

  it('moves and resizes the crop box without leaving the picture or losing its shape', () => {
    const r = { x: 0.25, y: 0.25, w: 0.5, h: 0.5 };
    expect(moveCrop(r, 1, -1)).toEqual({ x: 0.5, y: 0, w: 0.5, h: 0.5 });
    const free = resizeCrop(r, 'se', 0.1, 0.2, null, 100, 100);
    expect(free).toEqual({ x: 0.25, y: 0.25, w: expect.closeTo(0.6, 6), h: expect.closeTo(0.7, 6) });
    const nw = resizeCrop(r, 'nw', -1, -1, null, 100, 100);
    expect(nw).toEqual({ x: 0, y: 0, w: 0.75, h: 0.75 });
    const tiny = resizeCrop(r, 'se', -1, -1, null, 100, 100);
    expect(tiny.w).toBeCloseTo(MIN_CROP);
    // A 4:5 box on a 400x300 picture keeps 4:5 in pixels, and stays on the picture.
    const portrait = resizeCrop({ x: 0.1, y: 0.1, w: 0.3, h: 0.5 }, 'se', 0.5, 0, 4 / 5, 400, 300);
    expect((portrait.w * 400) / (portrait.h * 300)).toBeCloseTo(4 / 5);
    expect(portrait.y + portrait.h).toBeLessThanOrEqual(1 + 1e-9);
    expect(portrait.x + portrait.w).toBeLessThanOrEqual(1 + 1e-9);
  });
});

describe('applyRecipe', () => {
  it('never changes the frames it is given', () => {
    const f = grid(6, 4, 80);
    const copy = new Uint8ClampedArray(f.data);
    applyRecipe([f], { ...emptyRecipe(), filter: 'noir', rotate: 90, crop: { x: 0, y: 0, w: 0.5, h: 0.5 } });
    applyRecipe([f], { ...emptyRecipe(), filter: 'pop' });
    expect(f.data).toEqual(copy);
  });

  it('turns, then crops, then colours, keeping each frame its own timing', () => {
    const frames = [grid(8, 4, 50), grid(8, 4, 120)];
    const out = applyRecipe(frames, { ...emptyRecipe(), rotate: 90, crop: { x: 0, y: 0, w: 1, h: 0.5 } });
    expect(out.map((f) => [f.width, f.height, f.duration])).toEqual([
      [4, 4, 50],
      [4, 4, 120],
    ]);
    expect(outputSize(8, 4, { ...emptyRecipe(), rotate: 90, crop: { x: 0, y: 0, w: 1, h: 0.5 } }, { maxSide: 1e6, maxPixels: 1e9 })).toEqual({ width: 4, height: 4 });
  });

  it('brings the result within the limits', () => {
    const [out] = applyRecipe([grid(100, 50)], emptyRecipe(), { limits: { maxSide: 40, maxPixels: 1e9 } });
    expect([out.width, out.height]).toEqual([40, 20]);
    expect(outputSize(100, 50, emptyRecipe(), { maxSide: 40, maxPixels: 1e9 })).toEqual({ width: 40, height: 20 });
  });

  it('lays the overlay layer over every frame', () => {
    const layer = makeFrame(2, 2);
    layer.data.set([255, 0, 0, 255], 0); // top-left solid red
    layer.data.set([0, 0, 255, 128], 4); // top-right half blue
    const out = applyRecipe([solid(2, 2, [0, 255, 0, 255], 40), solid(2, 2, [0, 255, 0, 255], 60)], emptyRecipe(), { overlayLayer: layer });
    for (const f of out) {
      expect(px(f, 0, 0)).toEqual([255, 0, 0, 255]);
      const [r, g, b, a] = px(f, 1, 0);
      expect([r, a]).toEqual([0, 255]);
      expect(g).toBeGreaterThan(120);
      expect(b).toBeGreaterThan(120);
      expect(px(f, 0, 1)).toEqual([0, 255, 0, 255]);
    }
    expect(() => composite(solid(3, 3, [0, 0, 0, 255]), layer)).toThrow();
  });
});

describe('overlays', () => {
  it('draws, undoes, and keeps one drawing layer at the bottom', () => {
    let r: EditRecipe = { ...emptyRecipe(), overlays: [{ id: 'e', kind: 'emoji', emoji: '⭐', x: 0.5, y: 0.5, scale: 1, rotation: 0 }] };
    r = addStroke(r, { color: '#ffffff', width: 0.01, points: [[0, 0]] });
    r = addStroke(r, { color: '#000000', width: 0.01, points: [[1, 1]] });
    expect(r.overlays[0].kind).toBe('drawing');
    expect(drawingOf(r)!.strokes).toHaveLength(2);
    r = undoStroke(r);
    expect(drawingOf(r)!.strokes.map((s) => s.color)).toEqual(['#ffffff']);
    r = undoStroke(undoStroke(r));
    expect(drawingOf(r)!.strokes).toEqual([]);
    expect(removeOverlay(r, 'e').overlays.map((o) => o.id)).toEqual(['drawing']);
  });

  it('thins a finger line but keeps both ends', () => {
    const line = Array.from({ length: 101 }, (_, i) => [i / 10000, 0] as [number, number]);
    const thin = thinPoints(line, 0.004);
    expect(thin.length).toBeLessThan(10);
    expect(thin[0]).toEqual(line[0]);
    expect(thin[thin.length - 1]).toEqual(line[100]);
  });

  it('reads a pinch as bigger, turned and moved', () => {
    const c = pinchChange({ a: [0, 0], b: [100, 0] }, { a: [10, 10], b: [10, 210] });
    expect(c.scale).toBeCloseTo(2);
    expect(c.rotation).toBeCloseTo(90);
    expect([c.dx, c.dy]).toEqual([-40, 110]);
  });

  it('picks readable text on a pill', () => {
    expect(contrastOn('#ffffff')).toBe('#111827');
    expect(contrastOn('#111827')).toBe('#ffffff');
    expect(contrastOn('#facc15')).toBe('#111827');
  });

  it('ships its own simple stickers and family-friendly emoji', () => {
    expect(STICKERS.length).toBeGreaterThanOrEqual(8);
    for (const s of STICKERS) {
      const svg = stickerSvg(s.id)!;
      expect(svg).toMatch(/^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg" viewBox="0 0 100 100">/);
      // Drawn here: no outside picture, link or script.
      expect(svg).not.toMatch(/href|<image|<script|url\(|https?:\/\/(?!www\.w3\.org\/2000\/svg)/);
    }
    expect(stickerSvg('nope')).toBeNull();
    expect(new Set(EMOJI).size).toBe(EMOJI.length);
  });
});

describe('animated pictures', () => {
  it('tells a moving GIF, WebP or PNG from a still one by its headers', () => {
    expect(looksAnimated(fakeGif(2, 2, [[255, 0, 0], [0, 0, 255]]), 'image/gif')).toBe(true);
    expect(looksAnimated(fakeGif(2, 2, [[255, 0, 0]]), 'image/gif')).toBe(false);
    expect(looksAnimated(fakeWebpHeader(true), 'image/webp')).toBe(true);
    expect(looksAnimated(fakeWebpHeader(false), 'image/webp')).toBe(false);
    expect(looksAnimated(fakePngHeader(3), 'image/png')).toBe(true);
    expect(looksAnimated(fakePngHeader(), 'image/png')).toBe(false);
    expect(looksAnimated(new Uint8Array([0xff, 0xd8]), 'image/jpeg')).toBe(false);
  });

  it('shrinks the frames so a long animation stays within the work cap', () => {
    const one = animatedFrameSize(480, 270, 1);
    expect(one).toEqual({ width: 480, height: 270 });
    const many = animatedFrameSize(1920, 1080, 400);
    expect(many.width * many.height * MAX_FRAMES).toBeLessThanOrEqual(MAX_TOTAL_PIXELS * 1.01);
    expect(Math.max(many.width, many.height)).toBeLessThanOrEqual(720);
  });

  it('keeps frame timing, with the floor browsers use for zero delays', () => {
    expect(frameMs(70_000)).toBe(70);
    expect(frameMs(0)).toBe(100);
    expect(frameMs(null)).toBe(100);
    expect(frameMs(15_000)).toBe(20);
  });
});
