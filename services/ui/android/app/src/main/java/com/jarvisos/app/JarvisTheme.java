package com.jarvisos.app;

import android.content.Context;
import android.content.SharedPreferences;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * The user's selected Jarvis theme, for native surfaces (the lock screen and
 * the home-screen widgets) so they look exactly like the app.
 *
 * <p>Nothing here is a color of its own. The tokens come from, in order:
 * the full token set the web app mirrors through {@link TokenBridgePlugin#setTheme}
 * (any theme, imported packs included); the theme packs bundled into the APK
 * at build time from {@code services/ui/src/themes/packs} (so a phone that has
 * not opened the app since an update still matches); the guard's status
 * theme id picks which one.
 */
public final class JarvisTheme {

    public final String id;
    public final boolean dark;
    public final int bg;
    public final int surface;
    public final int text;
    public final int textMuted;
    public final int border;
    public final int accent;
    public final int onAccent;
    public final int accentAlt;
    public final int progress;
    public final int progressTrack;
    public final int ring;
    public final int ring2;
    public final int ring3;
    /** The neon glow, or 0 when the theme has none. */
    public final int glow;

    private JarvisTheme(String id, JSONObject t, Boolean schemeDark) {
        this.id = id;
        this.bg = cssColor(t.optString("bg", null), 0xFF000000);
        this.surface = cssColor(t.optString("surface", null), bg);
        this.text = cssColor(t.optString("text", null), 0xFFFFFFFF);
        this.textMuted = cssColor(t.optString("textMuted", null), text);
        this.border = cssColor(t.optString("border", null), textMuted);
        this.accent = cssColor(t.optString("accent", null), text);
        this.onAccent = cssColor(t.optString("onAccent", null), bg);
        this.accentAlt = cssColor(t.optString("accentAlt", null), accent);
        this.progress = cssColor(t.optString("progress", null), accent);
        this.progressTrack = cssColor(t.optString("progressTrack", null), border);
        this.ring = cssColor(t.optString("ring", null), progress);
        this.ring2 = cssColor(t.optString("ring2", null), accentAlt);
        this.ring3 = cssColor(t.optString("ring3", null), ring2);
        String g = t.isNull("glow") ? null : t.optString("glow", null);
        this.glow = g == null || g.isEmpty() ? 0 : cssColor(g, 0);
        String scheme = t.optString("scheme", "");
        if (schemeDark != null) this.dark = schemeDark;
        else if (!scheme.isEmpty()) this.dark = !"light".equalsIgnoreCase(scheme);
        else this.dark = luminance(bg) < 0.5;
    }

    /** The theme to paint with now. */
    public static JarvisTheme resolve(Context context) {
        SharedPreferences p = TokenBridgePlugin.prefs(context);
        String id = GuardApi.themeId(context);
        String mirroredId = p.getString(TokenBridgePlugin.KEY_THEME_ID, "");
        String mirrored = p.getString(TokenBridgePlugin.KEY_THEME_TOKENS, "");
        String scheme = p.getString(TokenBridgePlugin.KEY_THEME_SCHEME, "");
        Boolean schemeDark = scheme == null || scheme.isEmpty() ? null : !"light".equalsIgnoreCase(scheme);
        if (mirrored != null && !mirrored.isEmpty() && (id == null || id.isEmpty() || id.equals(mirroredId))) {
            try {
                return new JarvisTheme(mirroredId, new JSONObject(mirrored), schemeDark);
            } catch (Exception ignored) {
                // A damaged mirror falls through to the bundled packs.
            }
        }
        return bundled(context, id == null || id.isEmpty() ? mirroredId : id);
    }

    private static JSONArray packThemes;

    /** A theme from the packs bundled in the APK (the first one when the id is unknown). */
    static synchronized JarvisTheme bundled(Context context, String id) {
        if (packThemes == null) {
            packThemes = new JSONArray();
            try {
                String[] files = context.getAssets().list("theme_packs");
                if (files != null) {
                    java.util.Arrays.sort(files);
                    for (String f : files) {
                        JSONObject pack = new JSONObject(read(context, "theme_packs/" + f));
                        JSONArray themes = pack.optJSONArray("themes");
                        for (int i = 0; themes != null && i < themes.length(); i++) packThemes.put(themes.get(i));
                    }
                }
            } catch (Exception ignored) {
                // No packs bundled: the tokens fall back to each other below.
            }
        }
        JSONObject chosen = null;
        for (int i = 0; i < packThemes.length(); i++) {
            JSONObject t = packThemes.optJSONObject(i);
            if (t == null) continue;
            if (chosen == null) chosen = t;
            if (id != null && id.equals(t.optString("id"))) {
                chosen = t;
                break;
            }
        }
        if (chosen == null) return new JarvisTheme("", new JSONObject(), null);
        JSONObject tokens = chosen.optJSONObject("tokens");
        if (tokens == null) tokens = new JSONObject();
        if (chosen.has("scheme") && !tokens.has("scheme")) {
            try {
                tokens.put("scheme", chosen.optString("scheme"));
            } catch (Exception ignored) {
            }
        }
        return new JarvisTheme(chosen.optString("id"), tokens, null);
    }

    private static String read(Context context, String asset) throws Exception {
        StringBuilder b = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(context.getAssets().open(asset), StandardCharsets.UTF_8))) {
            String l;
            while ((l = r.readLine()) != null) b.append(l).append('\n');
        }
        return b.toString();
    }

    /** A signature that changes whenever the painted colors would. */
    public String signature() {
        return id + ":" + Integer.toHexString(accent) + ":" + Integer.toHexString(surface)
                + ":" + Integer.toHexString(text) + ":" + Integer.toHexString(ring2) + ":" + dark;
    }

    /**
     * A CSS color as an Android ARGB int, read the way the browser reads the
     * same token: #RGB, #RGBA, #RRGGBB, #RRGGBBAA (alpha last), rgb(), rgba().
     */
    public static int cssColor(String css, int fallback) {
        if (css == null) return fallback;
        String s = css.trim().toLowerCase(java.util.Locale.ROOT);
        try {
            if (s.startsWith("#")) {
                String h = s.substring(1);
                if (h.length() == 3 || h.length() == 4) {
                    StringBuilder b = new StringBuilder();
                    for (char c : h.toCharArray()) b.append(c).append(c);
                    h = b.toString();
                }
                if (h.length() == 6) return 0xFF000000 | (int) Long.parseLong(h, 16);
                if (h.length() == 8) {
                    int rgb = (int) Long.parseLong(h.substring(0, 6), 16);
                    int a = Integer.parseInt(h.substring(6, 8), 16);
                    return (a << 24) | rgb;
                }
                return fallback;
            }
            if (s.startsWith("rgb")) {
                int open = s.indexOf('(');
                int close = s.lastIndexOf(')');
                if (open < 0 || close <= open) return fallback;
                String[] parts = s.substring(open + 1, close).replace("/", ",").split("[,\\s]+");
                java.util.List<String> nums = new java.util.ArrayList<>();
                for (String part : parts) if (!part.isEmpty()) nums.add(part);
                if (nums.size() < 3) return fallback;
                int r = channel(nums.get(0));
                int g = channel(nums.get(1));
                int b = channel(nums.get(2));
                int a = 255;
                if (nums.size() >= 4) {
                    String av = nums.get(3);
                    float f = av.endsWith("%") ? Float.parseFloat(av.substring(0, av.length() - 1)) / 100f : Float.parseFloat(av);
                    a = Math.round(Math.max(0f, Math.min(1f, f)) * 255f);
                }
                return (a << 24) | (r << 16) | (g << 8) | b;
            }
        } catch (RuntimeException e) {
            return fallback;
        }
        return fallback;
    }

    private static int channel(String v) {
        float f = v.endsWith("%") ? Float.parseFloat(v.substring(0, v.length() - 1)) * 2.55f : Float.parseFloat(v);
        return Math.round(Math.max(0f, Math.min(255f, f)));
    }

    /** Relative luminance, 0 (black) to 1 (white). */
    public static double luminance(int c) {
        return (0.2126 * ((c >> 16) & 0xFF) + 0.7152 * ((c >> 8) & 0xFF) + 0.0722 * (c & 0xFF)) / 255.0;
    }

    /** {@code c1} moved toward {@code c2} by {@code ratio}, opaque. */
    public static int blend(int c1, int c2, float ratio) {
        float inv = 1f - ratio;
        int r = Math.round(((c1 >> 16) & 0xFF) * inv + ((c2 >> 16) & 0xFF) * ratio);
        int g = Math.round(((c1 >> 8) & 0xFF) * inv + ((c2 >> 8) & 0xFF) * ratio);
        int b = Math.round((c1 & 0xFF) * inv + (c2 & 0xFF) * ratio);
        return 0xFF000000 | (clamp(r) << 16) | (clamp(g) << 8) | clamp(b);
    }

    /** The same color at {@code alpha} (0..1). */
    public static int withAlpha(int color, float alpha) {
        int a = Math.round(255 * Math.max(0f, Math.min(1f, alpha))) & 0xFF;
        return (a << 24) | (color & 0x00FFFFFF);
    }

    private static int clamp(int v) {
        return Math.max(0, Math.min(255, v));
    }
}
