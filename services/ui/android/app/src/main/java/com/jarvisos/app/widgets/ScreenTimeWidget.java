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
 * Screen time on the home screen, in the user's Jarvis theme. A child sees
 * their own day (time left as a ring, what locks next, chores that earn
 * more); a parent sees each child (time left, locked or paused, a bar of the
 * day). Reads the same status the phone's guard uses.
 */
public class ScreenTimeWidget extends AppWidgetProvider {

    private static final int[] KID = {R.id.stw_kid_1, R.id.stw_kid_2, R.id.stw_kid_3};
    private static final int[] KID_NAME = {R.id.stw_kid_1_name, R.id.stw_kid_2_name, R.id.stw_kid_3_name};
    private static final int[] KID_VALUE = {R.id.stw_kid_1_value, R.id.stw_kid_2_value, R.id.stw_kid_3_value};
    private static final int[] KID_STATUS = {R.id.stw_kid_1_status, R.id.stw_kid_2_status, R.id.stw_kid_3_status};
    private static final int[] KID_BAR = {R.id.stw_kid_1_bar, R.id.stw_kid_2_bar, R.id.stw_kid_3_bar};

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushScreenTime(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushScreenTime(context);
        }
    }

    static JSONObject status(Context context, String username) throws Exception {
        return WidgetApi.get(context, "/api/screen-time/" + android.net.Uri.encode(username) + "/status");
    }

    static RemoteViews build(Context context) {
        RemoteViews base = buildBase(context);
        return WidgetFit.of(base)
                .at(100, 100, base, v -> fit(v, 100))
                .at(100, 150, base, v -> fit(v, 150))
                .at(100, 226, base, v -> fit(v, 226))
                .build();
    }

    /** A widget {@code h} dp tall: a smaller ring when short, and only the children's rows that fit. */
    private static void fit(RemoteViews v, float h) {
        if (h < 150) {
            WidgetFit.sizeDp(v, R.id.stw_ring, 62, 62);
            v.setTextViewTextSize(R.id.stw_left, android.util.TypedValue.COMPLEX_UNIT_SP, 13);
            v.setViewVisibility(R.id.stw_left_caption, View.GONE);
            v.setInt(R.id.stw_headline, "setMaxLines", 1);
            v.setViewVisibility(R.id.stw_line2, View.GONE);
        }
        int rows = WidgetFit.rowsFor(h, 52, 58, KID.length);
        for (int i = rows; i < KID.length; i++) v.setViewVisibility(KID[i], View.GONE);
    }

    static RemoteViews buildBase(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_screen_time);
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.stw_root, R.id.stw_icon, R.id.stw_title, R.id.stw_refresh, st.accent);
        views.setOnClickPendingIntent(R.id.stw_root, WidgetUpdater.openPathPendingIntent(context, "/guardian", 7200));
        views.setOnClickPendingIntent(R.id.stw_refresh, WidgetUpdater.refreshPendingIntent(context));
        st.text(views, R.id.stw_left, st.text);
        st.text(views, R.id.stw_left_caption, st.muted);
        st.text(views, R.id.stw_headline, st.text);
        st.text(views, R.id.stw_line1, st.muted);
        st.text(views, R.id.stw_line2, st.muted);
        st.progress(views, R.id.stw_ring, 100, 0, st.progress);
        if (WidgetApi.apiKey(context) == null) {
            st.status(views, R.id.stw_status, "Sign in to Jarvis OS");
            return views;
        }
        TimeZone tz = TimeZone.getDefault();
        // A parent (anyone who may manage the family) sees the children.
        List<JSONObject> kids = new ArrayList<>();
        try {
            JSONObject fam = WidgetApi.get(context, "/api/users/family");
            JSONArray items = fam.optJSONArray("items");
            for (int i = 0; items != null && i < items.length(); i++) {
                JSONObject m = items.optJSONObject(i);
                if (m != null && m.optBoolean("is_minor", false)) kids.add(m);
            }
        } catch (Exception notAParent) {
            // 403 for anyone who does not manage the family: show their own day.
        }
        if (!kids.isEmpty()) {
            views.setViewVisibility(R.id.stw_self, View.GONE);
            views.setViewVisibility(R.id.stw_family, View.VISIBLE);
            views.setTextViewText(R.id.stw_title, "SCREEN TIME · FAMILY");
            for (int i = 0; i < KID.length; i++) {
                if (i >= kids.size()) {
                    views.setViewVisibility(KID[i], View.GONE);
                    continue;
                }
                JSONObject kid = kids.get(i);
                String username = EntityNames.text(kid, "username");
                String name = EntityNames.text(kid, "display_name");
                JSONObject s = null;
                try {
                    s = status(context, username);
                } catch (Exception ignored) {
                }
                boolean locked = ScreenTimeFormat.locked(s);
                views.setViewVisibility(KID[i], View.VISIBLE);
                st.tile(views, KID[i], locked);
                views.setTextViewText(KID_NAME[i], name.isEmpty() ? username : name);
                views.setTextViewText(KID_VALUE[i], ScreenTimeFormat.value(s));
                views.setTextViewText(KID_STATUS[i], ScreenTimeFormat.status(s, tz));
                st.text(views, KID_NAME[i], st.text);
                st.text(views, KID_VALUE[i], locked ? st.accent : st.text);
                st.text(views, KID_STATUS[i], st.muted);
                int used = s == null ? 0 : s.optInt("used_min", 0);
                int limit = s == null ? 0 : s.optInt("limit_min", 0) + s.optInt("bonus_min", 0) + s.optInt("earned_min", 0);
                st.progress(views, KID_BAR[i], Math.max(limit, 1), limit > 0 ? used : 0, locked ? st.accent : st.progress);
                views.setOnClickPendingIntent(KID[i], WidgetUpdater.openPathPendingIntent(context,
                        "/family/member/" + android.net.Uri.encode(username) + "?section=time", 7210 + i));
            }
            return views;
        }
        String me = WidgetApi.prefs(context).getString(com.jarvisos.app.TokenBridgePlugin.KEY_USERNAME, "");
        if (me == null || me.isEmpty()) {
            st.status(views, R.id.stw_status, "Open Jarvis once to set this up");
            return views;
        }
        try {
            JSONObject s = status(context, me);
            boolean locked = ScreenTimeFormat.locked(s);
            views.setTextViewText(R.id.stw_left, ScreenTimeFormat.left(s));
            views.setTextViewText(R.id.stw_left_caption, locked ? "locked" : "left");
            views.setTextViewText(R.id.stw_headline, ScreenTimeFormat.headline(s));
            views.setTextViewText(R.id.stw_line1, ScreenTimeFormat.line1(s, tz));
            views.setTextViewText(R.id.stw_line2, ScreenTimeFormat.line2(s));
            st.progress(views, R.id.stw_ring, 100, ScreenTimeFormat.percentLeft(s), locked ? st.accent : st.progress);
            if (locked) st.text(views, R.id.stw_headline, st.accent);
        } catch (Exception e) {
            st.status(views, R.id.stw_status, WidgetApi.problem(e));
        }
        return views;
    }
}
