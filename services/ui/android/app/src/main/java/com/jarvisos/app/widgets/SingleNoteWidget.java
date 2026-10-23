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
 * One chosen note on the home screen.
 *
 * Checklist rows carry their own tap target, so a box can be ticked from the
 * home screen through {@code /api/communication/notes/check_off} without
 * opening the app. A note without checkboxes shows its first lines and opens
 * in the app when tapped. The note on disk is untouched: this is the same
 * markdown the Notes page and Nextcloud see.
 */
public class SingleNoteWidget extends AppWidgetProvider {
    static final int ROWS = 5;

    private static final int[] ROW_IDS = {
        R.id.note_row_1, R.id.note_row_2, R.id.note_row_3, R.id.note_row_4, R.id.note_row_5
    };

    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        WidgetUpdater.pushSingleNote(context);
        WidgetAlarm.schedule(context);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (AppWidgetManager.ACTION_APPWIDGET_UPDATE.equals(intent.getAction())) {
            WidgetUpdater.pushSingleNote(context);
        }
    }

    @Override
    public void onDeleted(Context context, int[] appWidgetIds) {
        super.onDeleted(context, appWidgetIds);
        if (appWidgetIds == null) return;
        for (int id : appWidgetIds) SingleNoteConfig.delete(context, id);
    }

    static RemoteViews build(Context context, int appWidgetId) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_single_note);
        views.setOnClickPendingIntent(R.id.single_note_refresh, WidgetUpdater.refreshPendingIntent(context));
        views.setOnClickPendingIntent(R.id.single_note_configure,
            WidgetUpdater.configureSingleNotePendingIntent(context, appWidgetId));
        for (int row = 0; row < ROWS; row++) {
            views.setViewVisibility(ROW_IDS[row], android.view.View.GONE);
        }
        views.setViewVisibility(R.id.single_note_body, android.view.View.GONE);
        views.setViewVisibility(R.id.single_note_hint, android.view.View.GONE);

        SingleNoteConfig config = SingleNoteConfig.load(context, appWidgetId);
        if (config == null) {
            views.setTextViewText(R.id.single_note_title, "Choose a note");
            views.setTextViewText(R.id.single_note_body, "Tap to pick a note for this widget.");
            views.setViewVisibility(R.id.single_note_body, android.view.View.VISIBLE);
            views.setOnClickPendingIntent(R.id.single_note_root,
                WidgetUpdater.configureSingleNotePendingIntent(context, appWidgetId));
            return views;
        }

        views.setTextViewText(R.id.single_note_title, config.title.isEmpty() ? "Note" : config.title);
        views.setOnClickPendingIntent(R.id.single_note_root,
            WidgetUpdater.openSingleNotePendingIntent(context, appWidgetId, config.path));

        String content = null;
        String error = null;
        if (WidgetApi.apiKey(context) == null) {
            error = "Sign in to Jarvis OS";
        } else {
            try {
                JSONObject body = new JSONObject();
                body.put("path", config.path);
                body.put("title", config.title);
                body.put("storage", "nextcloud");
                JSONObject reply = WidgetApi.post(context, "/api/communication/notes/read", body);
                content = reply.optString("message", "");
                if (content.isEmpty()) {
                    JSONObject detail = reply.optJSONObject("detail");
                    if (detail != null) content = detail.optString("content", "");
                }
                if (content.isEmpty()) error = "This note is empty";
            } catch (Exception ex) {
                error = ex.getMessage() != null ? ex.getMessage() : "offline";
            }
        }

        if (error != null) {
            views.setTextViewText(R.id.single_note_body, NotesLines.oneLine(error, 120));
            views.setViewVisibility(R.id.single_note_body, android.view.View.VISIBLE);
            return views;
        }

        List<NoteChecklist.Item> items = NoteChecklist.items(content, ROWS);
        if (!items.isEmpty()) {
            for (int row = 0; row < ROWS; row++) {
                if (row >= items.size()) break;
                NoteChecklist.Item item = items.get(row);
                views.setViewVisibility(ROW_IDS[row], android.view.View.VISIBLE);
                views.setTextViewText(ROW_IDS[row], (item.done ? "\u2611 " : "\u2610 ") + item.text);
                views.setTextColor(ROW_IDS[row], item.done ? 0xFF64748B : 0xFFE2E8F0);
                views.setOnClickPendingIntent(ROW_IDS[row],
                    WidgetUpdater.noteCheckOffPendingIntent(context, appWidgetId, config.path, config.title, item.text));
            }
            views.setTextViewText(R.id.single_note_hint, "Tap a box to tick it here.");
            views.setViewVisibility(R.id.single_note_hint, android.view.View.VISIBLE);
            return views;
        }

        List<String> lines = NoteChecklist.lines(content, ROWS);
        if (lines.isEmpty()) {
            views.setTextViewText(R.id.single_note_body, "This note is empty");
        } else {
            StringBuilder text = new StringBuilder();
            for (String line : lines) {
                if (text.length() > 0) text.append('\n');
                text.append(line);
            }
            views.setTextViewText(R.id.single_note_body, text.toString());
        }
        views.setViewVisibility(R.id.single_note_body, android.view.View.VISIBLE);
        views.setTextViewText(R.id.single_note_hint, "Tap to open and edit.");
        views.setViewVisibility(R.id.single_note_hint, android.view.View.VISIBLE);
        return views;
    }
}
