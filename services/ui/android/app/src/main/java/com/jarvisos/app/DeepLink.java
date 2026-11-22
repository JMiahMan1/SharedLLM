package com.jarvisos.app;

import java.io.UnsupportedEncodingException;
import java.net.URLEncoder;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import org.json.JSONObject;

/**
 * Where a tapped notice opens (docs/PUSH.md, "Deep links").
 *
 * A notice carries {@code data.link}: the page ({@code path}), the query the
 * page reads, a section anchor ({@code hash}) and {@code focus}, the one item
 * on the page it is about. This turns it into the in-app address the web app
 * navigates to; the web app's router then applies its own guards (admin pages,
 * Beloved, a locked private chat), so a link never opens more than a tap on
 * the page would. Older notices only have {@code data.url}.
 *
 * Free of Android imports so it runs in plain JVM tests.
 */
final class DeepLink {
    static final String FOCUS_PARAM = "focus";
    /** The data URI a notification's open intent carries ({@code jarvis://open/family?chat=x}). */
    static final String OPEN_URI_PREFIX = "jarvis://open";
    static final int MAX_LENGTH = 1000;

    private DeepLink() {}

    /** The page a notice opens: its link, else its in-app {@code url}, else empty. */
    static String forNotice(JSONObject data) {
        if (data == null) return "";
        String fromLink = fromLink(data.optJSONObject("link"));
        if (!fromLink.isEmpty()) return fromLink;
        return PushNotice.safePath(PushNotice.str(data, "url"));
    }

    /**
     * A structured link as an in-app address: path, then the query (keys
     * sorted, so the same link is always the same address) with {@code focus}
     * last, then the anchor. Empty when the path is not a page of the app.
     */
    static String fromLink(JSONObject link) {
        if (link == null) return "";
        String path = PushNotice.safePath(PushNotice.str(link, "path"));
        if (path.isEmpty() || path.indexOf('?') >= 0 || path.indexOf('#') >= 0) return "";
        StringBuilder out = new StringBuilder(path);
        List<String> parts = new ArrayList<>();
        JSONObject query = link.optJSONObject("query");
        if (query != null) {
            List<String> keys = new ArrayList<>();
            Iterator<String> it = query.keys();
            while (it.hasNext()) keys.add(it.next());
            Collections.sort(keys);
            for (String key : keys) {
                if (FOCUS_PARAM.equals(key)) continue;
                String value = PushNotice.str(query, key);
                if (value.isEmpty()) continue;
                parts.add(encode(key) + "=" + encode(value));
            }
        }
        String focus = PushNotice.str(link, "focus");
        if (!focus.isEmpty()) parts.add(FOCUS_PARAM + "=" + encode(focus));
        if (!parts.isEmpty()) out.append('?').append(String.join("&", parts));
        String hash = PushNotice.str(link, "hash");
        if (hash.startsWith("#")) hash = hash.substring(1);
        if (!hash.isEmpty() && hash.matches("[a-z0-9][a-z0-9_-]{0,63}")) out.append('#').append(hash);
        String href = out.toString();
        return href.length() > MAX_LENGTH ? "" : href;
    }

    /**
     * The page a tapped notification's intent asks for: the extra the
     * notification set, else the path in its {@code jarvis://open/...} data
     * URI (kept when extras are not, for example an intent delivered again
     * after the app was killed). Empty when neither is a page of the app.
     */
    static String fromIntent(String extraPath, String dataUri) {
        String extra = PushNotice.safePath(extraPath);
        if (!extra.isEmpty()) return extra;
        if (dataUri == null || !dataUri.startsWith(OPEN_URI_PREFIX + "/")) return "";
        return PushNotice.safePath(dataUri.substring(OPEN_URI_PREFIX.length()));
    }

    private static String encode(String value) {
        try {
            return URLEncoder.encode(value, "UTF-8").replace("+", "%20");
        } catch (UnsupportedEncodingException e) {
            return "";
        }
    }
}
