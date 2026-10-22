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

    static RemoteViews build(Context context) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_notes);
        views.setOnClickPendingIntent(R.id.notes_root, WidgetUpdater.openAppPendingIntent(context));
        views.setOnClickPendingIntent(R.id.notes_add, WidgetUpdater.composeNotePendingIntent(context));
        views.setOnClickPendingIntent(R.id.notes_refresh, WidgetUpdater.refreshPendingIntent(context));

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
                if (lines.isEmpty()) empty = "No notes yet";
            } catch (Exception ex) {
                String message = ex.getMessage() != null ? ex.getMessage() : "offline";
                empty = NotesLines.oneLine(message, 80);
            }
        }

        if (lines == null) {
            views.setViewVisibility(R.id.notes_empty, android.view.View.VISIBLE);
            views.setTextViewText(R.id.notes_empty, empty);
            for (int row = 1; row <= ROWS; row++) {
                views.setViewVisibility(rowId(row), android.view.View.GONE);
            }
            return views;
        }

        views.setViewVisibility(R.id.notes_empty, android.view.View.GONE);
        for (int row = 1; row <= ROWS; row++) {
            NotesLines.Line line = row <= lines.size() ? lines.get(row - 1) : null;
            int container = rowId(row);
            if (line == null) {
                views.setViewVisibility(container, android.view.View.GONE);
                continue;
            }
            views.setViewVisibility(container, android.view.View.VISIBLE);
            views.setTextViewText(titleId(row), line.title);
            views.setTextViewText(bodyId(row), line.body);
            views.setOnClickPendingIntent(container,
                WidgetUpdater.openNotePendingIntent(context, line.path, row));
        }
        return views;
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
