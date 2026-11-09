package com.jarvisos.app.widgets;

import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;
import com.jarvisos.app.R;
import java.util.List;
import org.json.JSONObject;

/**
 * Notes home-screen widget: the newest notes from {@code /Notes}.
 *
 * Display only. Tapping a row opens that note in the app; the plus button
 * opens a blank composer; the sync icon refreshes. Archive and Trash are
 * hidden here just as they are on the Notes page.
 */
public class NotesWidget extends AppWidgetProvider {
    static final int ROWS = 4;

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushNotes(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushNotes(context);
        }
    }

    private static final int[] ROW_IDS = {R.id.notes_row_1, R.id.notes_row_2, R.id.notes_row_3, R.id.notes_row_4};

    static RemoteViews build(Context context) {
        RemoteViews base = buildBase(context);
        return WidgetFit.of(base).rows(base, 100, ROW_IDS, 58, 50).build();
    }

    static RemoteViews buildBase(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_notes);
        views.setOnClickPendingIntent(R.id.notes_root, WidgetUpdater.openPathPendingIntent(context, "/notes", 6002));
        views.setOnClickPendingIntent(R.id.notes_add, WidgetUpdater.composeNotePendingIntent(context));
        views.setOnClickPendingIntent(R.id.notes_refresh, WidgetUpdater.refreshPendingIntent(context));
        WidgetStyle st = WidgetStyle.of(context);
        st.header(views, R.id.notes_root, R.id.notes_icon, R.id.notes_title, R.id.notes_refresh, st.fourth);
        st.fill(views, R.id.notes_add, R.drawable.wg_circle, st.accent);
        st.icon(views, R.id.notes_add, st.onAccent);
        st.text(views, R.id.notes_empty, st.muted);
        List<NotesLines.Line> lines = null;
        String empty = null;
        if (WidgetApi.apiKey(context) == null) {
            empty = "Sign in to Jarvis OS";
        } else {
            try {
                JSONObject body = new JSONObject();
                body.put("storage", "nextcloud");
                body.put("previews", true);
                JSONObject reply = WidgetApi.post(context, "/api/communication/notes/list", body);
                lines = NotesLines.recent(reply.optJSONObject("detail"), ROWS);
                if (lines.isEmpty()) {
                    empty = "No notes yet. Tap + to write one.";
                    lines = null;
                }
            } catch (Exception ex) {
                String message = ex.getMessage() != null ? ex.getMessage() : "offline";
                empty = NotesLines.oneLine(message, 80);
            }
        }
        for (int row = 1; row <= ROWS; row++) {
            NotesLines.Line line = lines != null && row <= lines.size() ? lines.get(row - 1) : null;
            int container = rowId(row);
            if (line == null) {
                views.setViewVisibility(container, android.view.View.GONE);
                continue;
            }
            views.setViewVisibility(container, android.view.View.VISIBLE);
            st.tile(views, container, false);
            st.icon(views, iconId(row), st.fourth);
            st.text(views, titleId(row), st.text);
            st.text(views, bodyId(row), st.muted);
            views.setTextViewText(titleId(row), line.title);
            views.setTextViewText(bodyId(row), line.body);
            views.setOnClickPendingIntent(container, WidgetUpdater.openNotePendingIntent(context, line.path, row));
        }
        views.setViewVisibility(R.id.notes_empty, lines == null ? android.view.View.VISIBLE : android.view.View.GONE);
        views.setTextViewText(R.id.notes_empty, empty == null ? "" : empty);
        return views;
    }

    private static int iconId(int row) {
        switch (row) {
            case 1: return R.id.notes_row_1_icon;
            case 2: return R.id.notes_row_2_icon;
            case 3: return R.id.notes_row_3_icon;
            default: return R.id.notes_row_4_icon;
        }
    }

    private static int rowId(int row) {
        switch (row) {
            case 1: return R.id.notes_row_1;
            case 2: return R.id.notes_row_2;
            case 3: return R.id.notes_row_3;
            default: return R.id.notes_row_4;
        }
    }

    private static int titleId(int row) {
        switch (row) {
            case 1: return R.id.notes_row_1_title;
            case 2: return R.id.notes_row_2_title;
            case 3: return R.id.notes_row_3_title;
            default: return R.id.notes_row_4_title;
        }
    }

    private static int bodyId(int row) {
        switch (row) {
            case 1: return R.id.notes_row_1_body;
            case 2: return R.id.notes_row_2_body;
            case 3: return R.id.notes_row_3_body;
            default: return R.id.notes_row_4_body;
        }
    }
}
