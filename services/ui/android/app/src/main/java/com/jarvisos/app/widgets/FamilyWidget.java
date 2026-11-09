package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.widget.RemoteViews;
import com.jarvisos.app.JarvisTheme;
import com.jarvisos.app.R;
import java.util.ArrayList;
import java.util.List;
import java.util.TimeZone;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * The family on the home screen, in the user's Jarvis theme: who's home and
 * who's out (only people who share their location with you) and unread family
 * chat.
 */
public class FamilyWidget extends AppWidgetProvider {

    private static final int[] P = {R.id.fm_p1, R.id.fm_p2, R.id.fm_p3, R.id.fm_p4, R.id.fm_p5, R.id.fm_p6};
    private static final int[] INITIAL = {R.id.fm_p1_initial, R.id.fm_p2_initial, R.id.fm_p3_initial, R.id.fm_p4_initial, R.id.fm_p5_initial, R.id.fm_p6_initial};
    private static final int[] NAME = {R.id.fm_p1_name, R.id.fm_p2_name, R.id.fm_p3_name, R.id.fm_p4_name, R.id.fm_p5_name, R.id.fm_p6_name};
    private static final int[] PLACE = {R.id.fm_p1_place, R.id.fm_p2_place, R.id.fm_p3_place, R.id.fm_p4_place, R.id.fm_p5_place, R.id.fm_p6_place};

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushFamily(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushFamily(context);
        }
    }

    static String place(String state) {
        if (state == null || state.isEmpty() || "unknown".equals(state) || "unavailable".equals(state)) return "Unknown";
        if ("home".equals(state)) return "Home";
        if ("not_home".equals(state)) return "Away";
        return Character.toUpperCase(state.charAt(0)) + state.substring(1).replace('_', ' ');
    }

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_family);
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.fm_root, R.id.fm_icon, R.id.fm_title, R.id.fm_refresh, st.accent);
        views.setOnClickPendingIntent(R.id.fm_root, WidgetUpdater.openPathPendingIntent(context, "/wander", 7250));
        views.setOnClickPendingIntent(R.id.fm_refresh, WidgetUpdater.refreshPendingIntent(context));
        views.setOnClickPendingIntent(R.id.fm_chat_chip, WidgetUpdater.openPathPendingIntent(context, "/family?tab=chat", 7251));
        st.fill(views, R.id.fm_chat_chip, R.drawable.wg_pill, st.accent);
        st.icon(views, R.id.fm_chat_icon, st.onAccent);
        st.text(views, R.id.fm_chat, st.onAccent);
        if (WidgetApi.apiKey(context) == null) {
            st.status(views, R.id.fm_status, "Sign in to Jarvis OS");
            return views;
        }
        String problem = null;
        List<JSONObject> people = new ArrayList<>();
        try {
            JSONObject geo = WidgetApi.get(context, "/api/geo/people");
            JSONArray features = geo.optJSONArray("features");
            for (int i = 0; features != null && i < features.length(); i++) {
                JSONObject f = features.optJSONObject(i);
                JSONObject props = f == null ? null : f.optJSONObject("properties");
                if (props != null) people.add(props);
            }
            // Home first, then everyone out, each by name.
            java.util.Collections.sort(people, (a, b) -> {
                boolean ha = "home".equals(EntityNames.text(a, "state"));
                boolean hb = "home".equals(EntityNames.text(b, "state"));
                if (ha != hb) return ha ? -1 : 1;
                return EntityNames.text(a, "friendly_name").compareToIgnoreCase(EntityNames.text(b, "friendly_name"));
            });
        } catch (Exception e) {
            problem = WidgetApi.problem(e);
        }
        int home = 0;
        for (int i = 0; i < P.length; i++) {
            if (i >= people.size()) {
                views.setViewVisibility(P[i], i < 3 || people.size() > 3 ? View.INVISIBLE : View.GONE);
                continue;
            }
            JSONObject p = people.get(i);
            String name = EntityNames.text(p, "friendly_name");
            String state = EntityNames.text(p, "state");
            boolean isHome = "home".equals(state);
            if (isHome) home++;
            views.setViewVisibility(P[i], View.VISIBLE);
            st.tile(views, P[i], isHome);
            views.setTextViewText(INITIAL[i], name.isEmpty() ? "?" : name.substring(0, 1).toUpperCase(java.util.Locale.ROOT));
            st.fill(views, INITIAL[i], R.drawable.wg_circle, isHome ? st.accent : JarvisTheme.blend(st.theme.surface, st.muted, 0.35f));
            st.text(views, INITIAL[i], isHome ? st.onAccent : st.text);
            views.setTextViewText(NAME[i], name.contains(" ") ? name.substring(0, name.indexOf(' ')) : name);
            views.setTextViewText(PLACE[i], place(state));
            st.text(views, NAME[i], st.text);
            st.text(views, PLACE[i], isHome ? st.accent : st.muted);
        }
        views.setViewVisibility(R.id.fm_row2, people.size() > 3 ? View.VISIBLE : View.GONE);
        if (!people.isEmpty()) views.setTextViewText(R.id.fm_title, "FAMILY · " + home + " HOME");
        // Unread family chat.
        try {
            JSONObject talk = WidgetApi.get(context, "/api/communication/talk/conversations");
            JSONObject detail = talk.optJSONObject("detail");
            JSONArray convs = detail == null ? null : detail.optJSONArray("conversations");
            int unread = 0;
            for (int i = 0; convs != null && i < convs.length(); i++) {
                JSONObject c = convs.optJSONObject(i);
                if (c != null) unread += c.optInt("unread_messages", 0);
            }
            if (unread > 0) {
                views.setViewVisibility(R.id.fm_chat_chip, View.VISIBLE);
                views.setTextViewText(R.id.fm_chat, unread > 99 ? "99+" : String.valueOf(unread));
            }
        } catch (Exception ignored) {
            // Chat off for this account: no chip.
        }
        if (people.isEmpty() && problem == null) problem = "No one is sharing their location with you";
        st.status(views, R.id.fm_status, people.isEmpty() ? problem : null);
        return views;
    }
}
