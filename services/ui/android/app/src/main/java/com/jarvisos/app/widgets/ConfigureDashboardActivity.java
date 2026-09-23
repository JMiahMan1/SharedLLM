package com.jarvisos.app.widgets;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.Intent;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.AutoCompleteTextView;
import android.widget.Button;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import com.jarvisos.app.R;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.json.JSONObject;

/** Launcher configure activity for DashboardWidget — 4 searchable entity cells. */
public class ConfigureDashboardActivity extends Activity {
    private int appWidgetId = AppWidgetManager.INVALID_APPWIDGET_ID;
    private final AutoCompleteTextView[] fields = new AutoCompleteTextView[DashboardConfig.MAX_CELLS];
    private final TextView[] previews = new TextView[DashboardConfig.MAX_CELLS];
    private ProgressBar entityProgress;
    private TextView entityStatus;
    private final List<String> entityIds = new ArrayList<>();
    private final List<String> entityLabels = new ArrayList<>();
    private final Map<String, String> liveStates = new ConcurrentHashMap<>();
    private final Map<String, String> liveNames = new ConcurrentHashMap<>();
    private ArrayAdapter<String> adapter;
    private int loadGeneration;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_configure_dashboard);

        appWidgetId = getIntent().getIntExtra(
            AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            finish();
            return;
        }

        fields[0] = findViewById(R.id.field_entity_1);
        fields[1] = findViewById(R.id.field_entity_2);
        fields[2] = findViewById(R.id.field_entity_3);
        fields[3] = findViewById(R.id.field_entity_4);
        previews[0] = findViewById(R.id.preview_1);
        previews[1] = findViewById(R.id.preview_2);
        previews[2] = findViewById(R.id.preview_3);
        previews[3] = findViewById(R.id.preview_4);
        entityProgress = findViewById(R.id.entity_progress);
        entityStatus = findViewById(R.id.entity_status);
        Button btnSave = findViewById(R.id.btn_save);
        Button btnCancel = findViewById(R.id.btn_cancel);

        List<String> saved = DashboardConfig.loadCells(this, appWidgetId);
        for (int i = 0; i < fields.length; i++) {
            if (i < saved.size()) fields[i].setText(saved.get(i));
            final int idx = i;
            fields[i].addTextChangedListener(new SimpleWatcher() {
                @Override public void afterTextChanged(Editable s) {
                    refreshPreview(idx);
                    // Live re-filter suggestions as the user types
                    if (adapter != null) adapter.getFilter().filter(s != null ? s.toString() : "");
                }
            });
            fields[i].setOnItemClickListener((parent, view, position, id) -> {
                String full = entityLabels.get(position);
                int open = full.lastIndexOf('(');
                int close = full.lastIndexOf(')');
                if (open >= 0 && close > open) {
                    fields[idx].setText(full.substring(open + 1, close));
                    fields[idx].setSelection(fields[idx].getText().length());
                }
                refreshPreview(idx);
                // Live-push so the home screen updates without waiting for Save
                applyLive(idx);
            });
        }

        btnSave.setOnClickListener(v -> save());
        btnCancel.setOnClickListener(v -> {
            setResult(Activity.RESULT_CANCELED);
            finish();
        });

        loadEntities();
    }

    private String textAt(int i) {
        CharSequence cs = fields[i].getText();
        return cs != null ? cs.toString().trim() : "";
    }

    private void refreshPreview(int i) {
        String id = textAt(i);
        if (id.isEmpty()) {
            previews[i].setText("");
            return;
        }
        String state = liveStates.get(id);
        String name = liveNames.get(id);
        if (name == null) {
            int dot = id.indexOf('.');
            name = dot >= 0 ? id.substring(dot + 1).replace('_', ' ') : id;
        }
        previews[i].setText(state != null ? name + " · " + state : name);
    }

    /** Propagate a single cell to the live widget as soon as an entity is chosen. */
    private void applyLive(int cellIndex) {
        List<String> cells = collectCells();
        // Persist only the cells so far so a partial config still renders
        DashboardConfig.saveCells(this, appWidgetId, cells);
        AppWidgetManager mgr = AppWidgetManager.getInstance(this);
        mgr.updateAppWidget(appWidgetId, DashboardWidget.build(this, appWidgetId));
        WidgetUpdater.request(this, DashboardWidget.class);
        // Keep collecting remaining cells in memory via prefs — save() rewrites full list
        if (cellIndex >= 0) {
            previews[cellIndex].setAlpha(1f);
        }
    }

    private List<String> collectCells() {
        List<String> cells = new ArrayList<>();
        for (int i = 0; i < fields.length; i++) {
            String id = textAt(i);
            if (!id.isEmpty()) cells.add(id);
        }
        return cells;
    }

    private void loadEntities() {
        if (WidgetApi.apiKey(this) == null) {
            entityStatus.setVisibility(View.VISIBLE);
            entityStatus.setText("Sign in to Jarvis OS to browse entities, or type an entity_id.");
            return;
        }
        entityProgress.setVisibility(View.VISIBLE);
        entityStatus.setVisibility(View.VISIBLE);
        entityStatus.setText("Loading entities…");
        final int gen = ++loadGeneration;
        WidgetUpdater.onBackground(() -> {
            final List<String> ids = new ArrayList<>();
            final List<String> labels = new ArrayList<>();
            final Map<String, String> states = new ConcurrentHashMap<>();
            final Map<String, String> names = new ConcurrentHashMap<>();
            final String[] error = new String[1];
            try {
                JSONObject all = WidgetApi.entityStates(this, null);
                java.util.Iterator<String> it = all.keys();
                while (it.hasNext()) {
                    String id = it.next();
                    JSONObject e = all.optJSONObject(id);
                    String friendly = e != null ? WidgetApi.friendlyName(e) : id;
                    String state = e != null ? e.optString("state", "") : "";
                    ids.add(id);
                    names.put(id, friendly);
                    states.put(id, state);
                    labels.add(friendly + "  (" + id + ")");
                }
            } catch (Exception e) {
                error[0] = e.getMessage() != null ? e.getMessage() : "Failed to load entities";
            }
            WidgetUpdater.onMain(() -> {
                if (gen != loadGeneration || isFinishing()) return;
                entityProgress.setVisibility(View.GONE);
                if (error[0] != null) {
                    entityStatus.setText(error[0] + " — type entity_id manually");
                    return;
                }
                if (ids.isEmpty()) {
                    entityStatus.setText("No entities found — type entity_id");
                    return;
                }
                entityIds.clear();
                entityIds.addAll(ids);
                entityLabels.clear();
                entityLabels.addAll(labels);
                liveStates.clear();
                liveStates.putAll(states);
                liveNames.clear();
                liveNames.putAll(names);
                entityStatus.setText(ids.size() + " entities — type to search");
                adapter = new ArrayAdapter<>(
                    ConfigureDashboardActivity.this,
                    android.R.layout.simple_dropdown_item_1line,
                    entityLabels);
                for (AutoCompleteTextView f : fields) {
                    f.setAdapter(adapter);
                }
                for (int i = 0; i < previews.length; i++) refreshPreview(i);
            });
        });
    }

    private void save() {
        List<String> cells = collectCells();
        if (cells.isEmpty()) {
            Toast.makeText(this, "Pick at least one entity", Toast.LENGTH_SHORT).show();
            return;
        }
        DashboardConfig.saveCells(this, appWidgetId, cells);
        AppWidgetManager mgr = AppWidgetManager.getInstance(this);
        mgr.updateAppWidget(appWidgetId, DashboardWidget.build(this, appWidgetId));
        WidgetUpdater.request(this, DashboardWidget.class);

        Intent result = new Intent();
        result.putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, appWidgetId);
        setResult(Activity.RESULT_OK, result);
        finish();
    }

    private abstract static class SimpleWatcher implements TextWatcher {
        @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
        @Override public void onTextChanged(CharSequence s, int a, int b, int c) {}
        @Override public void afterTextChanged(Editable s) {}
    }
}
