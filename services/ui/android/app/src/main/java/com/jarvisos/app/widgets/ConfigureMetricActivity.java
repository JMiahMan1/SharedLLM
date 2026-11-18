package com.jarvisos.app.widgets;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.Intent;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.List;
import org.json.JSONObject;

/** Launcher configure activity for MetricWidget: pick one of the home's sensors that reads a number. */
public class ConfigureMetricActivity extends Activity {
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
        heading.setText("Choose a sensor");
        heading.setTextColor(0xFFE2E8F0);
        heading.setTextSize(20);
        root.addView(heading);

        TextView sub = new TextView(this);
        sub.setText("The widget shows this sensor's reading on the home screen.");
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
        com.jarvisos.app.SystemBarPadding.apply(this, 0xFF0F172A);
        loadSensors();
    }

    private void loadSensors() {
        final ConfigureMetricActivity activity = this;
        WidgetUpdater.onBackground(() -> {
            try {
                WidgetApi.ensureCredentials(activity);
                if (WidgetApi.apiKey(activity) == null) {
                    WidgetUpdater.onMain(() -> {
                        progress.setVisibility(View.GONE);
                        status.setText("Sign in to Jarvis OS first.");
                    });
                    return;
                }
                JSONObject byId = WidgetApi.entityStates(activity, null);
                final List<MetricChoices.Choice> rows = MetricChoices.from(byId);
                WidgetUpdater.onMain(() -> showSensors(rows));
            } catch (Exception e) {
                String message = e.getMessage() != null ? e.getMessage() : "Could not load sensors";
                WidgetUpdater.onMain(() -> {
                    progress.setVisibility(View.GONE);
                    status.setText(message);
                });
            }
        });
    }

    private void showSensors(List<MetricChoices.Choice> rows) {
        progress.setVisibility(View.GONE);
        if (rows.isEmpty()) {
            status.setText("No sensors with a number reading were found in your home.");
            return;
        }
        for (MetricChoices.Choice row : rows) {
            LinearLayout item = new LinearLayout(this);
            item.setOrientation(LinearLayout.VERTICAL);
            item.setGravity(Gravity.CENTER_VERTICAL);
            item.setPadding(dp(14), dp(12), dp(14), dp(12));
            item.setBackgroundColor(0xFF1E293B);
            item.setMinimumHeight(dp(48));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            lp.setMargins(0, 0, 0, dp(8));
            item.setLayoutParams(lp);

            TextView name = new TextView(this);
            name.setText(row.name);
            name.setTextColor(0xFFE2E8F0);
            name.setTextSize(16);
            item.addView(name);

            TextView reading = new TextView(this);
            reading.setText(row.reading);
            reading.setTextColor(0xFF94A3B8);
            reading.setTextSize(13);
            reading.setMaxLines(1);
            reading.setEllipsize(TextUtils.TruncateAt.END);
            item.addView(reading);

            item.setContentDescription(row.name + ", " + row.reading);
            item.setOnClickListener(v -> choose(row));
            list.addView(item);
        }
    }

    private void choose(MetricChoices.Choice row) {
        WidgetApi.prefs(this).edit()
            .putString(MetricWidget.PREF_ENTITY, row.entityId)
            .putString(MetricWidget.PREF_LABEL, row.name)
            .apply();
        WidgetUpdater.pushMetric(this);
        Intent result = new Intent();
        result.putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId);
        setResult(Activity.RESULT_OK, result);
        finish();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
