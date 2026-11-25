import { describe, expect, it } from 'vitest';
import { eyesGeometry, LENS_SIZE, LENS_VIEW_WIDTH, LENSES, lensById, lensDataUrl, lensPlacement, lensSvg, type PhotoLens } from './lenses';

describe('the lens catalogue', () => {
  it('has a lens for every kind of thing to wear and they are all distinct', () => {
    expect(LENSES.length).toBeGreaterThanOrEqual(12);
    const ids = LENSES.map((l) => l.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain('glasses');
    expect(ids).toContain('sunglasses');
    expect(ids).toContain('party-hat');
    expect(ids).toContain('cat-ears');
    expect(ids).toContain('crown');
  });

  it('every lens is something a child can wear in a family picture', () => {
    // The catalogue is fixed and drawn here; this keeps a later addition honest.
    const unkind = /\b(cigarette|smoke|vape|beer|wine|alcohol|blood|knife|gun|skull|demon|horror|rude)\b/i;
    for (const lens of LENSES) {
      expect(lens.label, lens.id).toMatch(/^[A-Za-z ]+$/);
      expect(lens.label, lens.id).not.toMatch(unkind);
      expect(lens.svg, lens.id).not.toMatch(/<script|javascript:|on\w+=/i);
      expect(lens.span, lens.id).toBeGreaterThan(0.3);
      expect(lens.span, lens.id).toBeLessThan(4);
      expect(lens.aspect, lens.id).toBeGreaterThan(0.5);
      expect(lens.aspect, lens.id).toBeLessThan(4);
      expect(lens.origin[0], lens.id).toBeGreaterThanOrEqual(0);
      expect(lens.origin[1], lens.id).toBeGreaterThanOrEqual(0);
      expect(lens.origin[0], lens.id).toBeLessThanOrEqual(1);
      expect(lens.origin[1], lens.id).toBeLessThanOrEqual(1);
    }
  });

  it('draws each lens in a box as wide as the arithmetic expects and as tall as its own shape', () => {
    for (const lens of LENSES) {
      const svg = lensSvg(lens.id)!;
      const height = Math.round(LENS_VIEW_WIDTH / lens.aspect);
      expect(svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg"'), lens.id).toBe(true);
      expect(svg, lens.id).toContain(`viewBox="0 0 ${LENS_VIEW_WIDTH} ${height}"`);
      expect(svg.endsWith('</svg>'), lens.id).toBe(true);
    }
    expect(lensSvg('no-such-lens')).toBeNull();
    expect(lensById('no-such-lens')).toBeNull();
  });

  it('offers each lens as a picture the page and the canvas can both load', () => {
    const url = lensDataUrl('glasses')!;
    expect(url.startsWith('data:image/svg+xml;charset=utf-8,')).toBe(true);
    expect(decodeURIComponent(url.split(',')[1])).toContain('<svg');
    expect(lensDataUrl('no-such-lens')).toBeNull();
  });
});

describe('fitting a lens to a face', () => {
  const level = eyesGeometry([80, 100], [120, 100], [100, 130]);

  it('reads the eye line from the two pupils, however the picture was taken', () => {
    expect(level.eyeMid).toEqual([100, 100]);
    expect(level.eyeDistance).toBeCloseTo(40);
    expect(level.angle).toBeCloseTo(0);
    expect(level.nose).toEqual([100, 130]);

    // A mirrored picture hands the same eyes over the other way round; the
    // lens must not end up upside down because of it.
    const mirrored = eyesGeometry([120, 100], [80, 100]);
    expect(mirrored.eyeMid).toEqual([100, 100]);
    expect(Math.abs(mirrored.angle)).toBeLessThanOrEqual(90);

    // A head tilted the other way is within a quarter turn either way.
    const tilted = eyesGeometry([80, 90], [120, 110]);
    expect(tilted.angle).toBeCloseTo(26.57, 1);
    const tiltedBack = eyesGeometry([120, 110], [80, 90]);
    expect(tiltedBack.angle).toBeCloseTo(26.57, 1);
  });

  it('puts glasses over the eyes at the size of the face', () => {
    const glasses = lensById('glasses')!;
    const at = lensPlacement(glasses, level, { width: 400, height: 600 });
    expect(at.x).toBeCloseTo(100 / 400);
    expect(at.y).toBeCloseTo(100 / 600);
    expect(at.rotation).toBeCloseTo(0);
    // The box is span eye distances wide, and a scale of 1 covers LENS_SIZE of the picture.
    expect(at.scale).toBeCloseTo((glasses.span * 40) / (LENS_SIZE * 400));
    expect(at.scale).toBeGreaterThan(0.25);
    expect(at.scale).toBeLessThan(6);
  });

  it('lifts a hat over the head along the face, not along the picture', () => {
    const hat = lensById('party-hat')!;
    const upright = lensPlacement(hat, level, { width: 400, height: 600 });
    expect(upright.y).toBeCloseTo((100 - (hat.lift ?? 1.05) * 40) / 600);
    expect(upright.x).toBeCloseTo(100 / 400);

    // A head turned a quarter turn: "up the face" is now across the picture.
    const sideways = lensPlacement(hat, { ...level, angle: 90 }, { width: 400, height: 600 });
    expect(sideways.x).toBeCloseTo((100 + (hat.lift ?? 1.05) * 40) / 400);
    expect(sideways.y).toBeCloseTo(100 / 600);
    expect(sideways.rotation).toBeCloseTo(90);
  });

  it('hangs a nose lens on the nose', () => {
    const nose: PhotoLens = { ...lensById('clown-nose')!, anchor: 'nose' };
    const at = lensPlacement(nose, level, { width: 400, height: 600 });
    expect(at.x).toBeCloseTo(100 / 400);
    expect(at.y).toBeCloseTo(130 / 600);
  });
});
