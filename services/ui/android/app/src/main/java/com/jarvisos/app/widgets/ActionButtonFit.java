package com.jarvisos.app.widgets;

/**
 * How the action button fills a widget of a given size, like an app icon:
 * the round button takes most of the cell and the name sits under it, whole.
 *
 * <p>The name is never cut: it gets up to two lines, then a smaller size on up
 * to three, and the on/off line goes before the button is squeezed. Only when
 * no usable button fits beside it does the name go (a tiny widget is the
 * button alone; its name is still read out by TalkBack). Pure arithmetic in
 * dp, so it is tested without a device.
 */
final class ActionButtonFit {

    /** The card's own padding on each side (widget_action_button.xml). */
    static final float PAD = 3f;
    /** Gap between the button and the name. */
    static final float LABEL_GAP = 3f;
    /** One line of the name at 12sp and at the smaller 10sp. */
    static final float LINE = 15f;
    static final float SMALL_LINE = 12.5f;
    /** The on/off line (10sp plus its gap). */
    static final float STATE = 13f;
    /** Average width of one character of the name at 12sp bold; 10sp is 5/6 of it. */
    static final float CHAR = 6.6f;
    /** A button smaller than this is not worth a name beside it. */
    static final float MIN_WITH_LABEL = 26f;
    /** The name's lines at 12sp, and at the smaller 10sp before it is hidden. */
    static final int MAX_LINES = 2;
    static final int MAX_SMALL_LINES = 3;
    /** The on/off line shows only while the button still fills this share of the cell. */
    static final float STATE_SHARE = 0.7f;
    /** The glyph's share of the button. */
    static final float GLYPH = 0.62f;
    static final float MAX_BUTTON = 132f;

    final float button;
    /** Inner padding around the glyph, dp. */
    final float glyphPad;
    final boolean label;
    final boolean state;
    /** Name lines and whether it uses the smaller size. */
    final int lines;
    final boolean smallText;

    private ActionButtonFit(float button, boolean label, boolean state, int lines, boolean smallText) {
        this.button = button;
        this.glyphPad = Math.round(button * (1 - GLYPH) / 2f);
        this.label = label;
        this.state = state;
        this.lines = lines;
        this.smallText = smallText;
    }

    /** Lines {@code text} needs across {@code width} dp at a character width. */
    static int linesFor(String text, float width, float charDp) {
        String t = text == null ? "" : text.trim();
        if (t.isEmpty()) return 0;
        int perLine = Math.max(1, (int) Math.floor(width / charDp));
        // Words wrap whole, so count a line per word that would not fit after the last.
        int lines = 1;
        int used = 0;
        for (String word : t.split("\\s+")) {
            int len = word.length();
            if (used == 0) {
                used = len;
            } else if (used + 1 + len <= perLine) {
                used += 1 + len;
            } else {
                lines++;
                used = len;
            }
            while (used > perLine) { // a word longer than a line breaks inside it
                lines++;
                used -= perLine;
            }
        }
        return lines;
    }

    static ActionButtonFit of(float widthDp, float heightDp, String name, String stateText) {
        float w = Math.max(0, widthDp - 2 * PAD);
        float h = Math.max(0, heightDp - 2 * PAD);
        float side = Math.min(w, h);
        boolean hasName = name != null && !name.trim().isEmpty();
        boolean hasState = stateText != null && !stateText.trim().isEmpty();
        if (hasName) {
            int lines = linesFor(name, w, CHAR);
            int smallLines = linesFor(name, w, CHAR * 5f / 6f);
            float nameH = LABEL_GAP + lines * LINE;
            float smallH = LABEL_GAP + smallLines * SMALL_LINE;
            // Best first: the state line too while the button keeps most of the cell; then the
            // name alone at full size; then the name smaller (up to three lines) before it is hidden.
            if (hasState && lines <= MAX_LINES) {
                float b = Math.min(w, h - nameH - STATE);
                if (b >= side * STATE_SHARE) return new ActionButtonFit(cap(b), true, true, lines, false);
            }
            if (lines <= MAX_LINES) {
                float b = Math.min(w, h - nameH);
                if (b >= MIN_WITH_LABEL) return new ActionButtonFit(cap(b), true, false, lines, false);
            }
            if (smallLines <= MAX_SMALL_LINES) {
                float b = Math.min(w, h - smallH);
                if (b >= MIN_WITH_LABEL) return new ActionButtonFit(cap(b), true, false, smallLines, true);
            }
        }
        return new ActionButtonFit(cap(side), false, false, 0, false);
    }

    private static float cap(float dp) {
        return Math.max(16f, Math.min(MAX_BUTTON, (float) Math.floor(dp)));
    }
}
