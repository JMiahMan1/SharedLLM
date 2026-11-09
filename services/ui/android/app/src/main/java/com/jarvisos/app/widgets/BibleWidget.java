package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.view.View;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Bible on the home screen, in the user's Jarvis theme.
 *
 * <p>Full size (the hub): today's verse, Continue reading where you left off,
 * today's plan reading, and the reading streak, daily quiz and memory verses.
 * Small size: the three stats. Android 12+ picks the size the launcher gives
 * it; every tap opens the matching Bible page.
 */
public class BibleWidget extends AppWidgetProvider {

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushBible(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushBible(context);
        }
    }

    /** Everything the widget reads, fetched once for both sizes. */
    static final class Data {
        boolean signedIn;
        JSONObject daily;
        JSONObject state;
        JSONObject plans;
        JSONObject quiz;
        JSONObject due;
        String problem;

        JSONObject streaks() {
            return daily == null ? null : daily.optJSONObject("streaks");
        }
    }

    static Data fetch(Context context) {
        Data d = new Data();
        d.signedIn = WidgetApi.apiKey(context) != null;
        if (!d.signedIn) return d;
        // Independent reads: one failing leaves the others' tiles honest.
        try { d.daily = WidgetApi.get(context, "/api/bible/daily"); } catch (Exception e) { d.problem = e.getMessage(); }
        try { d.state = WidgetApi.get(context, "/api/bible/state"); } catch (Exception e) { if (d.problem == null) d.problem = e.getMessage(); }
        try { d.plans = WidgetApi.get(context, "/api/bible/plans"); } catch (Exception ignored) { }
        try { d.quiz = WidgetApi.get(context, "/api/bible/games/leaderboard"); } catch (Exception ignored) { }
        try { d.due = WidgetApi.get(context, "/api/bible/memorize/due?limit=100"); } catch (Exception ignored) { }
        return d;
    }

    private static final int[] S_TILE = {R.id.bw_streak_tile, R.id.bw_quiz_tile, R.id.bw_due_tile};
    private static final int[] S_LABEL = {R.id.bw_streak_label, R.id.bw_quiz_label, R.id.bw_due_label};
    private static final int[] S_VALUE = {R.id.bw_streak_value, R.id.bw_quiz_value, R.id.bw_due_value};
    private static final int[] S_CAPTION = {R.id.bw_streak_caption, R.id.bw_quiz_caption, R.id.bw_due_caption};

    static RemoteViews build(Context context) {
        Data d = fetch(context);
        RemoteViews compact = buildCompact(context, d);
        RemoteViews full = buildFull(context, d);
        WidgetFit fit = WidgetFit.of(full)
                .at(100, 80, compact, WidgetFit.stats(S_TILE, S_LABEL, S_VALUE, S_CAPTION, true, true))
                .at(100, 150, compact, v -> {
                    WidgetFit.stats(S_TILE, S_LABEL, S_VALUE, S_CAPTION, true, false).apply(v);
                    // "verses to review" is three lines in a narrow tile.
                    if (d.due != null && d.due.optInt("count", 0) > 0) v.setTextViewText(R.id.bw_due_caption, "to review");
                })
                .at(250, 80, compact, WidgetFit.stats(S_TILE, S_LABEL, S_VALUE, S_CAPTION, false, true))
                .at(250, 150, compact)
                .at(240, 230, full);
        return fit.build();
    }

    static RemoteViews buildCompact(Context context) {
        return buildCompact(context, fetch(context));
    }

    static RemoteViews buildFull(Context context) {
        return buildFull(context, fetch(context));
    }

    private static void tile(RemoteViews views, int valueId, int captionId, StatsWidgetFormat.Tile tile) {
        views.setTextViewText(valueId, tile.value);
        views.setTextViewText(captionId, tile.caption);
    }

    static RemoteViews buildCompact(Context context, Data d) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_bible);
        views.setOnClickPendingIntent(R.id.bw_root, WidgetUpdater.openPathPendingIntent(context, "/bible", 7110));
        views.setOnClickPendingIntent(R.id.bw_streak_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=progress", 7111));
        views.setOnClickPendingIntent(R.id.bw_quiz_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=play", 7112));
        views.setOnClickPendingIntent(R.id.bw_due_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=memorize", 7113));
        views.setOnClickPendingIntent(R.id.bw_refresh, WidgetUpdater.refreshPendingIntent(context));
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.bw_root, R.id.bw_icon, R.id.bw_title, R.id.bw_refresh, st.accent);
        st.stat(views, R.id.bw_streak_tile, R.id.bw_streak_icon, R.id.bw_streak_label,
                R.id.bw_streak_value, R.id.bw_streak_caption, st.fourth, false);
        st.stat(views, R.id.bw_quiz_tile, R.id.bw_quiz_icon, R.id.bw_quiz_label,
                R.id.bw_quiz_value, R.id.bw_quiz_caption, st.accent, false);
        st.stat(views, R.id.bw_due_tile, R.id.bw_due_icon, R.id.bw_due_label,
                R.id.bw_due_value, R.id.bw_due_caption, st.second, false);
        if (!d.signedIn) {
            st.status(views, R.id.bw_status, "Sign in to Jarvis OS");
            return views;
        }
        JSONObject streaks = d.streaks();
        tile(views, R.id.bw_streak_value, R.id.bw_streak_caption, StatsWidgetFormat.streak(streaks));
        tile(views, R.id.bw_quiz_value, R.id.bw_quiz_caption, StatsWidgetFormat.quiz(d.quiz));
        tile(views, R.id.bw_due_value, R.id.bw_due_caption, StatsWidgetFormat.due(d.due));
        if (streaks != null && streaks.optInt("read_streak_current", 0) > 0) st.tileTint(views, R.id.bw_streak_tile, st.fourth);
        if ("Play".equals(StatsWidgetFormat.quiz(d.quiz).value)) st.tile(views, R.id.bw_quiz_tile, true);
        if (d.due != null && d.due.optInt("count", 0) > 0) st.tile(views, R.id.bw_due_tile, true);
        boolean nothing = d.daily == null && d.quiz == null && d.due == null;
        st.status(views, R.id.bw_status, nothing ? (d.problem != null ? d.problem : "Offline") : null);
        return views;
    }

    static RemoteViews buildFull(Context context, Data d) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_bible_hub);
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.bh_root, R.id.bh_icon, R.id.bh_title, R.id.bh_refresh, st.accent);
        views.setOnClickPendingIntent(R.id.bh_root, WidgetUpdater.openPathPendingIntent(context, "/bible", 7114));
        views.setOnClickPendingIntent(R.id.bh_refresh, WidgetUpdater.refreshPendingIntent(context));
        views.setOnClickPendingIntent(R.id.bh_verse_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=today", 7115));
        views.setOnClickPendingIntent(R.id.bh_read_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=read", 7116));
        views.setOnClickPendingIntent(R.id.bh_plan_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=today", 7117));
        views.setOnClickPendingIntent(R.id.bh_quiz_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=play", 7118));
        views.setOnClickPendingIntent(R.id.bh_due_tile, WidgetUpdater.openPathPendingIntent(context, "/bible?tab=memorize", 7119));
        st.tile(views, R.id.bh_verse_tile, false);
        st.text(views, R.id.bh_verse, st.text);
        st.text(views, R.id.bh_reference, st.accent);
        paintAction(st, views, R.id.bh_read_tile, R.id.bh_read_icon, R.id.bh_read_label, R.id.bh_read_value, R.id.bh_read_caption, st.accent);
        paintAction(st, views, R.id.bh_plan_tile, R.id.bh_plan_icon, R.id.bh_plan_label, R.id.bh_plan_value, R.id.bh_plan_caption, st.second);
        st.tile(views, R.id.bh_quiz_tile, false);
        st.tile(views, R.id.bh_due_tile, false);
        st.icon(views, R.id.bh_quiz_icon, st.accent);
        st.icon(views, R.id.bh_due_icon, st.second);
        st.text(views, R.id.bh_quiz_text, st.text);
        st.text(views, R.id.bh_due_text, st.text);
        st.fill(views, R.id.bh_chip, R.drawable.wg_pill, com.jarvisos.app.JarvisTheme.blend(st.theme.surface, st.fourth, 0.22f));
        st.icon(views, R.id.bh_chip_icon, st.fourth);
        st.text(views, R.id.bh_streak, st.text);

        if (!d.signedIn) {
            views.setTextViewText(R.id.bh_verse, "Sign in to Jarvis OS to see today's verse, your reading and your plan.");
            return views;
        }

        // Today's verse.
        VerseDaily.Lines lines = d.daily == null
                ? new VerseDaily.Lines("", "-", "", "", "")
                : VerseDaily.fromJson(d.daily);
        views.setTextViewText(R.id.bh_verse, lines.text);
        views.setTextViewText(R.id.bh_reference, lines.reference);

        // Streak chip.
        JSONObject streaks = d.streaks();
        int streak = streaks == null ? 0 : streaks.optInt("read_streak_current", 0);
        if (streak > 0) {
            views.setViewVisibility(R.id.bh_chip, View.VISIBLE);
            views.setTextViewText(R.id.bh_streak, streak + (streak == 1 ? " day" : " days"));
        }

        // Continue reading where you left off.
        BibleHub.Continue cont = BibleHub.continueReading(d.state);
        views.setTextViewText(R.id.bh_read_value, cont.value);
        views.setTextViewText(R.id.bh_read_caption, cont.caption);
        if (cont.ref != null) {
            views.setOnClickPendingIntent(R.id.bh_read_tile, WidgetUpdater.openPathPendingIntent(context,
                    "/bible?tab=read&ref=" + Uri.encode(cont.ref), 7116));
        }

        // Today's plan reading (opens the passage itself).
        BibleHub.Plan plan = BibleHub.todaysPlan(d.plans);
        views.setTextViewText(R.id.bh_plan_value, plan.value);
        views.setTextViewText(R.id.bh_plan_caption, plan.caption);
        if (plan.ref != null) {
            views.setOnClickPendingIntent(R.id.bh_plan_tile, WidgetUpdater.openPathPendingIntent(context,
                    "/bible?tab=read&ref=" + Uri.encode(plan.ref), 7117));
            st.tile(views, R.id.bh_plan_tile, true);
        }

        // Quiz and memory.
        StatsWidgetFormat.Tile quiz = StatsWidgetFormat.quiz(d.quiz);
        views.setTextViewText(R.id.bh_quiz_text, "Play".equals(quiz.value) ? "Play today's quiz" : "Quiz " + quiz.value);
        if ("Play".equals(quiz.value)) st.tile(views, R.id.bh_quiz_tile, true);
        StatsWidgetFormat.Tile due = StatsWidgetFormat.due(d.due);
        views.setTextViewText(R.id.bh_due_text, "-".equals(due.value) || "-".equals(due.value) ? "Memory verses"
                : "0".equals(due.value) ? "Memory: all caught up" : due.value + " to review");
        if (d.due != null && d.due.optInt("count", 0) > 0) st.tile(views, R.id.bh_due_tile, true);

        boolean nothing = d.daily == null && d.state == null && d.quiz == null && d.due == null;
        st.status(views, R.id.bh_status, nothing ? (d.problem != null ? d.problem : "Offline") : null);
        return views;
    }

    private static void paintAction(WidgetStyle st, RemoteViews v, int tile, int icon, int label, int value, int caption, int glyph) {
        st.tile(v, tile, false);
        st.icon(v, icon, glyph);
        st.text(v, label, st.muted);
        st.text(v, value, st.text);
        st.text(v, caption, st.muted);
    }
}
