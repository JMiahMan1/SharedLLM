package com.jarvisos.app.widgets;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;

/** Launcher configure activity for SingleNoteWidget: pick one note. */
public class ConfigureSingleNoteActivity extends Activity {
    private int appWidgetId = AppWidgetManager.INVALID_APPWIDGET_ID;
    private LinearLayout list;
    private ProgressBar progress;
    private TextView status;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        appWidgetId = getIntent().getIntExtra(
            AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            finish();
            return;
        }
        setResult(Activity.RESULT_CANCELED);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xFF0F172A);
        int pad = dp(20);
        root.setPadding(pad, pad, pad, pad);

        TextView heading = new TextView(this);
        heading.setText("Choose a note");
        heading.setTextColor(0xFFE2E8F0);
        heading.setTextSize(20);
        root.addView(heading);

        TextView sub = new TextView(this);
        sub.setText("The widget shows this note on the home screen; checklist boxes can be ticked there.");
        sub.setTextColor(0xFF94A3B8);
        sub.setTextSize(13);
        sub.setPadding(0, dp(6), 0, dp(14));
        root.addView(sub);

        progress = new ProgressBar(this);
        progress.setIndeterminate(true);
        root.addView(progress);

        status = new TextView(this);
        status.setTextColor(0xFFF87171);
        status.setTextSize(13);
        status.setPadding(0, dp(8), 0, dp(8));
        root.addView(status);

        ScrollView scroll = new ScrollView(this);
        scroll.setLayoutParams(new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(list);
        root.addView(scroll);

        Button cancel = new Button(this);
        cancel.setText("Cancel");
        cancel.setOnClickListener(v -> {
            setResult(Activity.RESULT_CANCELED);
            finish();
        });
        root.addView(cancel);

        setContentView(root);
        loadNotes();
    }

    private void loadNotes() {
        final ConfigureSingleNoteActivity activity = this;
        WidgetUpdater.onBackground(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("previews", false);
                body.put("storage", "nextcloud");
                JSONObject reply = WidgetApi.post(activity, "/api/communication/notes/list", body);
                JSONObject detail = reply.optJSONObject("detail");
                JSONArray notes = detail != null ? detail.optJSONArray("notes") : null;
                final java.util.List<String[]> rows = new java.util.ArrayList<>();
                if (notes != null) {
                    for (int i = 0; i < notes.length(); i++) {
                        JSONObject note = notes.optJSONObject(i);
                        if (note == null) continue;
                        String path = note.optString("path", "");
                        if (path.isEmpty() || !NotesLines.visiblePath(path)) continue;
                        String title = note.optString("title", "");
                        if (title.trim().isEmpty()) title = "Untitled note";
                        rows.add(new String[] {title, path});
                    }
                }
                WidgetUpdater.onMain(() -> showNotes(rows));
            } catch (Exception e) {
                String message = e.getMessage() != null ? e.getMessage() : "Could not load notes";
                WidgetUpdater.onMain(() -> {
                    progress.setVisibility(View.GONE);
                    status.setText(message);
                });
            }
        });
    }

    private void showNotes(java.util.List<String[]> rows) {
        progress.setVisibility(View.GONE);
        if (rows.isEmpty()) {
            status.setText("No notes found. Write one in the Notes page first.");
            return;
        }
        for (String[] row : rows) {
            final String title = row[0];
            final String path = row[1];
            TextView item = new TextView(this);
            item.setText(title);
            item.setTextColor(0xFFE2E8F0);
            item.setTextSize(16);
            item.setGravity(Gravity.CENTER_VERTICAL);
            item.setPadding(dp(14), dp(14), dp(14), dp(14));
            item.setBackgroundColor(0xFF1E293B);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            lp.setMargins(0, 0, 0, dp(8));
            item.setLayoutParams(lp);
            item.setOnClickListener(v -> choose(title, path));
            list.addView(item);
        }
    }

    private void choose(String title, String path) {
        SingleNoteConfig.save(this, appWidgetId, new SingleNoteConfig(path, title));
        WidgetUpdater.pushSingleNote(this);
        Intent result = new Intent();
        result.putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId);
        setResult(Activity.RESULT_OK, result);
        finish();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
