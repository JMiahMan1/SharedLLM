package com.jarvisos.app.widgets;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

/** Desktop-JVM tests for centring an icon on what it draws. */
public class GlyphBoundsTest {

    @Test
    public void findsTheInkNotTheBox() {
        // A 10x10 bitmap whose shape sits low and left: rows 5..8, columns 1..4.
        int[] px = new int[100];
        for (int y = 5; y <= 8; y++) for (int x = 1; x <= 4; x++) px[y * 10 + x] = 0xFF000000;
        assertArrayEquals(new int[] {1, 5, 5, 9}, GlyphBounds.ofAlpha(px, 10, 10));
        assertNull(GlyphBounds.ofAlpha(new int[100], 10, 10));
    }

    @Test
    public void aShapeIsCentredAndAsLargeAsFits() {
        // A wide shape in a 100 square with a 10 margin: 80 wide, centred both ways.
        assertArrayEquals(new float[] {10f, 30f, 90f, 70f}, GlyphBounds.fitArray(40, 20, 100, 10), 0.001f);
        // A tall one: 80 high, centred across.
        assertArrayEquals(new float[] {30f, 10f, 70f, 90f}, GlyphBounds.fitArray(20, 40, 100, 10), 0.001f);
    }
}
