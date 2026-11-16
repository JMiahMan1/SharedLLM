import { describe, expect, it } from 'vitest';
import { exifDate, readExif } from './exif';
import { fakeJpeg } from '../test/fakes/fakeJpeg';

const local = (y: number, mo: number, d: number, h: number, mi: number, s: number) => Math.floor(new Date(y, mo - 1, d, h, mi, s).getTime() / 1000);

describe('the EXIF reader', () => {
  it('reads when it was taken, the camera, orientation and GPS (big endian)', () => {
    const info = readExif(
      fakeJpeg({ dateTimeOriginal: '2019:05:12 14:30:05', make: 'Acme', model: 'Pocket 7', orientation: 6, gps: { lat: 12.5, lon: -45.25, alt: 120 } }),
    );
    expect(info.takenAt).toBe(local(2019, 5, 12, 14, 30, 5));
    expect(info.zoned).toBeUndefined();
    expect(info.make).toBe('Acme');
    expect(info.model).toBe('Pocket 7');
    expect(info.orientation).toBe(6);
    expect(info.gps?.lat).toBeCloseTo(12.5, 4);
    expect(info.gps?.lon).toBeCloseTo(-45.25, 4);
    expect(info.gps?.alt).toBeCloseTo(120, 1);
  });

  it('reads little endian files and uses the time zone offset when there is one', () => {
    const info = readExif(fakeJpeg({ dateTimeOriginal: '2020:01:02 03:04:05', offsetTimeOriginal: '+09:00', littleEndian: true, gps: { lat: -33.5, lon: 10, alt: -5 } }));
    expect(info.takenAt).toBe(Date.UTC(2020, 0, 1, 18, 4, 5) / 1000);
    expect(info.zoned).toBe(true);
    expect(info.gps?.lat).toBeCloseTo(-33.5, 4);
    expect(info.gps?.alt).toBeCloseTo(-5, 1);
  });

  it('gives nothing for a JPEG without EXIF, a PNG, or junk, and never throws', () => {
    expect(readExif(fakeJpeg())).toEqual({});
    expect(readExif(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0, 0, 0, 0]))).toEqual({});
    expect(readExif(new Uint8Array([]))).toEqual({});
    const broken = fakeJpeg({ dateTimeOriginal: '2019:05:12 14:30:05' }).slice(0, 30);
    expect(() => readExif(broken)).not.toThrow();
  });

  it('ignores a 0,0 GPS fix and impossible dates', () => {
    expect(readExif(fakeJpeg({ gps: { lat: 0, lon: 0 } })).gps).toBeUndefined();
    expect(exifDate('0000:00:00 00:00:00')).toBeUndefined();
    expect(exifDate('2019:13:01 00:00:00')).toBeUndefined();
    expect(exifDate(undefined)).toBeUndefined();
  });
});
