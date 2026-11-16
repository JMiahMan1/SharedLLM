package com.jarvisos.app.widgets;

import android.graphics.Bitmap;
import android.graphics.Rect;
import android.graphics.RectF;

/**
 * Where an icon really draws, and where to put it so it looks centred.
 *
 * <p>Icon artwork does not always sit in the middle of its box (a house shape
 * leaves more room above than below), so centring the box leaves the shape off
 * centre. The arithmetic is pure so it is tested on the JVM; {@link #of}
 * reads a bitmap.
 */
final class GlyphBounds {

    private GlyphBounds() {}

    /** The smallest rectangle holding every pixel with any alpha, or null when the bitmap is empty. */
    static Rect of(Bitmap bitmap) {
        int w = bitmap.getWidth();
        int h = bitmap.getHeight();
        int[] px = new int[w * h];
        bitmap.getPixels(px, 0, w, 0, 0, w, h);
        int[] box = ofAlpha(px, w, h);
        return box == null ? null : new Rect(box[0], box[1], box[2], box[3]);
    }

    /** [left, top, right, bottom) of the pixels whose alpha is not 0, or null. */
    static int[] ofAlpha(int[] argb, int w, int h) {
        int left = w, top = h, right = -1, bottom = -1;
        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                if ((argb[y * w + x] >>> 24) != 0) {
                    if (x < left) left = x;
                    if (x > right) right = x;
                    if (y < top) top = y;
                    if (y > bottom) bottom = y;
                }
            }
        }
        return right < 0 ? null : new int[] {left, top, right + 1, bottom + 1};
    }

    /** The rectangle a {@code w}x{@code h} shape fills in a {@code side} square less {@code margin}, centred. */
    static RectF fit(int w, int h, int side, int margin) {
        float[] r = fitArray(w, h, side, margin);
        return new RectF(r[0], r[1], r[2], r[3]);
    }

    static float[] fitArray(int w, int h, int side, int margin) {
        float room = side - 2f * margin;
        float scale = room / Math.max(w, h);
        float fw = w * scale;
        float fh = h * scale;
        float left = (side - fw) / 2f;
        float top = (side - fh) / 2f;
        return new float[] {left, top, left + fw, top + fh};
    }
}
