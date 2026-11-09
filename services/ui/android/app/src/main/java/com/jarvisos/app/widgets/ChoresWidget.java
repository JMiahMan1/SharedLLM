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
 * Today's chores on the home screen, in the user's Jarvis theme: each chore
 * ticked or not with the stars it earns, and a bar of the day.
 */
public class ChoresWidget extends AppWidgetProvider {

    private static final int[] ROW = {R.id.ch_c1, R.id.ch_c2, R.id.ch_c3, R.id.ch_c4, R.id.ch_c5};
    private static final int[] CHECK = {R.id.ch_c1_check, R.id.ch_c2_check, R.id.ch_c3_check, R.id.ch_c4_check, R.id.ch_c5_check};
    private static final int[] EMOJI = {R.id.ch_c1_emoji, R.id.ch_c2_emoji, R.id.ch_c3_emoji, R.id.ch_c4_emoji, R.id.ch_c5_emoji};
    private static final int[] TITLE = {R.id.ch_c1_title, R.id.ch_c2_title, R.id.ch_c3_title, R.id.ch_c4_title, R.id.ch_c5_title};
    private static final int[] REWARD = {R.id.ch_c1_reward, R.id.ch_c2_reward, R.id.ch_c3_reward, R.id.ch_c4_reward, R.id.ch_c5_reward};

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushChores(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushChores(context);
        }
    }

    static RemoteViews build(Context context) {
        RemoteViews base = buildBase(context);
        return WidgetFit.of(base).rows(base, 100, ROW, 63, 31).build();
    }

    static RemoteViews buildBase(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_chores);
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.ch_root, R.id.ch_icon, R.id.ch_title, R.id.ch_refresh, st.accent);
        views.setOnClickPendingIntent(R.id.ch_root, WidgetUpdater.openPathPendingIntent(context, "/", 7240));
        views.setOnClickPendingIntent(R.id.ch_refresh, WidgetUpdater.refreshPendingIntent(context));
        st.text(views, R.id.ch_count, st.muted);
        st.text(views, R.id.ch_empty, st.muted);
        st.progress(views, R.id.ch_bar, 1, 0, st.progress);
        if (WidgetApi.apiKey(context) == null) {
            empty(views, "Sign in to Jarvis OS");
            return views;
        }
        try {
            JSONObject r = WidgetApi.get(context, "/api/integrations/skylight/chores?date=today&scope=me");
            JSONArray chores = r.optJSONArray("chores");
            int total = chores == null ? 0 : chores.length();
            int done = 0;
            List<JSONObject> list = new ArrayList<>();
            for (int i = 0; i < total; i++) {
                JSONObject c = chores.optJSONObject(i);
                if (c == null) continue;
                if (c.optBoolean("completed", false)) done++;
                list.add(c);
            }
            // Still to do first, then what's done.
            java.util.Collections.sort(list, (a, b) -> Boolean.compare(a.optBoolean("completed"), b.optBoolean("completed")));
            views.setTextViewText(R.id.ch_count, total == 0 ? "" : done + " of " + total + " done");
            st.progress(views, R.id.ch_bar, Math.max(total, 1), done, done == total && total > 0 ? st.third : st.progress);
            for (int i = 0; i < ROW.length; i++) {
                if (i >= list.size()) {
                    views.setViewVisibility(ROW[i], View.GONE);
                    continue;
                }
                JSONObject c = list.get(i);
                boolean ok = c.optBoolean("completed", false);
                views.setViewVisibility(ROW[i], View.VISIBLE);
                views.setImageViewResource(CHECK[i], ok ? R.drawable.mi_check_box : R.drawable.mi_check_box_blank);
                st.icon(views, CHECK[i], ok ? st.third : st.muted);
                views.setTextViewText(EMOJI[i], EntityNames.text(c, "emoji_icon"));
                views.setTextViewText(TITLE[i], EntityNames.text(c, "title"));
                st.text(views, TITLE[i], ok ? st.faint : st.text);
                views.setInt(TITLE[i], "setPaintFlags", ok
                        ? (android.graphics.Paint.STRIKE_THRU_TEXT_FLAG | android.graphics.Paint.ANTI_ALIAS_FLAG)
                        : android.graphics.Paint.ANTI_ALIAS_FLAG);
                int stars = c.optInt("stars", 0);
                String reward = stars > 0 ? "★ " + stars : EntityNames.text(c, "reward");
                views.setTextViewText(REWARD[i], reward);
                st.text(views, REWARD[i], st.fourth);
            }
            if (list.isEmpty()) empty(views, "No chores today. Enjoy!");
            else views.setViewVisibility(R.id.ch_empty, View.GONE);
        } catch (Exception e) {
            String p = WidgetApi.problem(e);
            empty(views, p.toLowerCase(java.util.Locale.ROOT).contains("skylight") ? "Chores aren't set up for you yet" : p);
        }
        return views;
    }

    private static void empty(RemoteViews views, String message) {
        for (int id : ROW) views.setViewVisibility(id, View.GONE);
        views.setViewVisibility(R.id.ch_empty, View.VISIBLE);
        views.setTextViewText(R.id.ch_empty, message);
    }
}
