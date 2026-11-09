package com.jarvisos.app.widgets;

import android.os.Build;
import android.util.SizeF;
import android.widget.RemoteViews;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Responsive widgets: one RemoteViews per size class, so a widget dropped at
 * 2x2 (or resized smaller) shows a layout made for that size instead of the
 * roomy one squeezed until its text disappears.
 *
 * <p>Android 12+ picks the largest entry that fits the widget's current size
 * (or the smallest when none fits) and switches on resize without a refresh.
 * Older Android has no sizes, so it gets the fallback (the default size).
 *
 * <p>Typical use: build the default views once (one network fetch), then add
 * variants that are copies of it with a few tweaks on top, e.g. fewer rows.
 */
final class WidgetFit {

    /** A tweak applied on top of a copy of the base views. */
    interface Tweak {
        void apply(RemoteViews views);
    }

    /** DEBUG preview: when set, {@link #build()} returns only the entry the launcher would pick at this size. */
    static volatile SizeF preview;

    private final RemoteViews fallback;
    private final Map<SizeF, RemoteViews> sizes = new LinkedHashMap<>();

    private WidgetFit(RemoteViews fallback) {
        this.fallback = fallback;
    }

    /** Start from the views older Android shows (the widget's default size). */
    static WidgetFit of(RemoteViews fallback) {
        return new WidgetFit(fallback);
    }

    /** Show {@code views} when the widget is at least {@code w}x{@code h} dp. */
    WidgetFit at(float w, float h, RemoteViews views) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) sizes.put(new SizeF(w, h), views);
        return this;
    }

    /** Show a copy of {@code base} with {@code tweak} applied when at least {@code w}x{@code h} dp. */
    WidgetFit at(float w, float h, RemoteViews base, Tweak tweak) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return this;
        RemoteViews copy = new RemoteViews(base);
        tweak.apply(copy);
        sizes.put(new SizeF(w, h), copy);
        return this;
    }

    /** On Android before 12 (no sizes), apply {@code tweak} to the fallback itself. */
    WidgetFit fallback(Tweak tweak) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) tweak.apply(fallback);
        return this;
    }

    /** Resize a view in dp (Android 12+; the tweaks that call this only run there). */
    static void sizeDp(RemoteViews v, int id, float w, float h) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return;
        v.setViewLayoutWidth(id, w, android.util.TypedValue.COMPLEX_UNIT_DIP);
        v.setViewLayoutHeight(id, h, android.util.TypedValue.COMPLEX_UNIT_DIP);
    }

    /**
     * A list widget: one entry per row count, so a short widget shows the rows
     * that fit instead of a half-cut one at the bottom. {@code chromeDp} is
     * everything that isn't a row (padding, header, footer).
     */
    WidgetFit rows(RemoteViews base, float minW, int[] rowIds, float chromeDp, float rowDp) {
        for (int n = 1; n <= rowIds.length; n++) {
            final int keep = n;
            float h = n == 1 ? 0 : chromeDp + n * rowDp;
            at(minW, h, base, v -> {
                for (int i = keep; i < rowIds.length; i++) v.setViewVisibility(rowIds[i], android.view.View.GONE);
            });
        }
        return this;
    }

    /**
     * The three-stat cards (Games, Bible) when small: narrow drops the word
     * labels (the icons stay) and tightens the tiles; short drops the captions
     * and lowers the number so the tiles fit one row of cells.
     */
    static Tweak stats(int[] tiles, int[] labels, int[] values, int[] captions, boolean narrow, boolean low) {
        return v -> {
            for (int i = 0; i < tiles.length; i++) {
                int pad = narrow ? 6 : 10;
                int vpad = low ? 5 : 10;
                v.setViewPadding(tiles[i], px(pad), px(vpad), px(pad), px(vpad));
                if (narrow) v.setViewVisibility(labels[i], android.view.View.GONE);
                if (narrow && !low) {
                    // Captions wrap in a narrow tile ("games / played") instead of "gam…".
                    v.setBoolean(captions[i], "setSingleLine", false);
                    v.setInt(captions[i], "setMaxLines", 2);
                }
                if (low) {
                    v.setViewVisibility(captions[i], android.view.View.GONE);
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                        v.setViewLayoutHeight(values[i], 22, android.util.TypedValue.COMPLEX_UNIT_DIP);
                        v.setViewLayoutMargin(values[i], RemoteViews.MARGIN_TOP, 3, android.util.TypedValue.COMPLEX_UNIT_DIP);
                    }
                }
            }
        };
    }

    private static int px(float dp) {
        return Math.round(dp * android.content.res.Resources.getSystem().getDisplayMetrics().density);
    }

    RemoteViews build() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || sizes.isEmpty()) return fallback;
        SizeF p = preview;
        if (p != null) return pick(p);
        return new RemoteViews(sizes);
    }

    /** Android's rule: the largest (by area) entry that fits, else the smallest. */
    private RemoteViews pick(SizeF at) {
        List<SizeF> keys = new ArrayList<>(sizes.keySet());
        SizeF best = null;
        SizeF smallest = null;
        for (SizeF k : keys) {
            if (smallest == null || area(k) < area(smallest)) smallest = k;
            if (k.getWidth() <= at.getWidth() && k.getHeight() <= at.getHeight()
                    && (best == null || area(k) > area(best))) best = k;
        }
        return sizes.get(best != null ? best : smallest);
    }

    private static float area(SizeF s) {
        return s.getWidth() * s.getHeight();
    }

    /** How many list rows of {@code rowDp} fit under a header in a widget {@code heightDp} tall. */
    static int rowsFor(float heightDp, float chromeDp, float rowDp, int max) {
        int n = (int) Math.floor((heightDp - chromeDp) / rowDp);
        return Math.max(1, Math.min(max, n));
    }
}
