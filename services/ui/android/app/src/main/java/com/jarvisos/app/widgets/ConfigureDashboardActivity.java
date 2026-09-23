package com.jarvisos.app.widgets;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.Intent;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
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
    private final EditText[] fields = new EditText[DashboardConfig.MAX_CELLS];
    private final ListView[] resultLists = new ListView[DashboardConfig.MAX_CELLS];
    private final TextView[] previews = new TextView[DashboardConfig.MAX_CELLS];
    private ProgressBar entityProgress;
    private TextView entityStatus;
    private final List<String> entityIds = new ArrayList<>();
    private final List<String> entityLabels = new ArrayList<>();
    private final Map<String, String> liveStates = new ConcurrentHashMap<>();
    private final Map<String, String> liveNames = new ConcurrentHashMap<>();
    private ArrayAdapter<String>[] resultsAdapters;
    private int loadGeneration;
    private int searchGeneration;
    private int activeCell = -1;
    private android.os.Handler searchHandler;
    /** True only while pickEntity() writes a cell — blocks that cell's results from reopening. */
    private final boolean[] suppressCellResults = new boolean[DashboardConfig.MAX_CELLS];

    @SuppressWarnings("unchecked")
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
        resultLists[0] = findViewById(R.id.entity_results_1);
        resultLists[1] = findViewById(R.id.entity_results_2);
        resultLists[2] = findViewById(R.id.entity_results_3);
        resultLists[3] = findViewById(R.id.entity_results_4);
        resultsAdapters = new ArrayAdapter[DashboardConfig.MAX_CELLS];
        searchHandler = new android.os.Handler(getMainLooper());
        for (int i = 0; i < fields.length; i++) {
            resultsAdapters[i] = makeDarkAdapter();
            resultLists[i].setAdapter(resultsAdapters[i]);
            final int idx = i;
            resultLists[i].setOnItemClickListener((parent, view, position, id) -> {
                String full = resultsAdapters[idx].getItem(position);
                if (full != null) pickEntity(idx, full);
            });
        }
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
                    if (suppressCellResults[idx]) {
                        if (resultLists[idx] != null) resultLists[idx].setVisibility(View.GONE);
                        return;
                    }
                    String q = s != null ? s.toString().trim() : "";
                    updateResultsList(idx, q);
                    scheduleServerSearch(idx, q);
                }
            });
            fields[i].setOnFocusChangeListener((v, hasFocus) -> {
                if (hasFocus) {
                    activeCell = idx;
                    if (!suppressCellResults[idx]) updateResultsList(idx, textAt(idx));
                }
            });
        }

        btnSave.setOnClickListener(v -> save());
        btnCancel.setOnClickListener(v -> {
            setResult(Activity.RESULT_CANCELED);
            finish();
        });

        loadEntities();
    }

    private ArrayAdapter<String> makeDarkAdapter() {
        ArrayAdapter<String> a = new ArrayAdapter<String>(
            this, android.R.layout.simple_list_item_1, new ArrayList<String>()) {
            @Override public android.view.View getView(int position, android.view.View convertView, android.view.ViewGroup parent) {
                android.view.View v = super.getView(position, convertView, parent);
                if (v instanceof TextView) {
                    TextView tv = (TextView) v;
                    tv.setTextColor(0xFFF1F5F9);
                    tv.setBackgroundColor(0xFF1E293B);
                    tv.setPadding(MaterialIcons.dp(getResources(), 12),
                        MaterialIcons.dp(getResources(), 10),
                        MaterialIcons.dp(getResources(), 12),
                        MaterialIcons.dp(getResources(), 10));
                }
                return v;
            }
        };
        a.setNotifyOnChange(true);
        return a;
    }

    private void updateResultsList(int cell, String query) {
        if (resultLists[cell] == null || resultsAdapters[cell] == null) return;
        if (entityLabels.isEmpty()) {
            resultLists[cell].setVisibility(View.GONE);
            return;
        }
        String q = query == null ? "" : query.toLowerCase();
        List<String> matches = new ArrayList<>();
        for (int i = 0; i < entityLabels.size(); i++) {
            String label = entityLabels.get(i);
            String id = i < entityIds.size() ? entityIds.get(i) : "";
            if (q.isEmpty()
                || label.toLowerCase().contains(q)
                || id.toLowerCase().contains(q)) {
                matches.add(label);
                if (!q.isEmpty() && matches.size() >= 40) break;
                if (q.isEmpty() && matches.size() >= 8) break;
            }
        }
        resultsAdapters[cell].clear();
        resultsAdapters[cell].addAll(matches);
        resultsAdapters[cell].notifyDataSetChanged();
        resultLists[cell].setVisibility(matches.isEmpty() ? View.GONE : View.VISIBLE);
    }

    private void pickEntity(int cell, String full) {
        int open = full.lastIndexOf('(');
        int close = full.lastIndexOf(')');
        if (searchHandler != null) searchHandler.removeCallbacksAndMessages(null);
        searchGeneration++; // drop any in-flight server search
        loadGeneration++;   // drop in-flight initial load so it can't reopen the list
        suppressCellResults[cell] = true;
        try {
            if (open >= 0 && close > open) {
                fields[cell].setText(full.substring(open + 1, close));
                fields[cell].setSelection(fields[cell].getText().length());
            }
        } finally {
            suppressCellResults[cell] = false;
        }
        refreshPreview(cell);
        if (resultLists[cell] != null) resultLists[cell].setVisibility(View.GONE);
        if (entityStatus != null) entityStatus.setVisibility(View.GONE);
        applyLive(cell);
    }

    private String textAt(int i) {
        CharSequence cs = fields[i].getText();
        return cs != null ? cs.toString().trim() : "";
    }

    /** Debounced server-side search with current app credentials for one cell. */
    private void scheduleServerSearch(int cell, String query) {
        if (searchHandler == null) return;
        final String q = query == null ? "" : query;
        final int idx = cell;
        if (q.isEmpty()) {
            updateResultsList(idx, "");
            return;
        }
        final int gen = ++searchGeneration;
        searchHandler.removeCallbacksAndMessages(null);
        searchHandler.postDelayed(() -> WidgetUpdater.onBackground(() -> {
            final List<String> ids = new ArrayList<>();
            final List<String> labels = new ArrayList<>();
            final Map<String, String> states = new ConcurrentHashMap<>();
            final Map<String, String> names = new ConcurrentHashMap<>();
            final String[] error = new String[1];
                try {
                    WidgetApi.ensureCredentials(this);
                    if (WidgetApi.apiKey(this) == null) {
                        error[0] = "Sign in to Jarvis OS to search entities.";
                    } else {
                        JSONObject found = WidgetApi.searchEntities(this, q, 40);
                        java.util.Iterator<String> it = found.keys();
                        while (it.hasNext()) {
                            String id = it.next();
                            JSONObject e = found.optJSONObject(id);
                            String friendly = e != null ? WidgetApi.friendlyName(e) : id;
                            String state = e != null ? e.optString("state", "") : "";
                            if (!isControllableEntity(id)) continue;
                            ids.add(id);
                            names.put(id, friendly);
                            states.put(id, state);
                            labels.add(friendly + "  (" + id + ")");
                        }
                    }
                } catch (Exception e) {
                error[0] = e.getMessage() != null ? e.getMessage() : "Search failed";
            }
            WidgetUpdater.onMain(() -> {
                if (gen != searchGeneration || isFinishing()) return;
                entityProgress.setVisibility(View.GONE);
                if (error[0] != null) {
                    entityStatus.setVisibility(View.VISIBLE);
                    entityStatus.setText(error[0]);
                    updateResultsList(idx, q);
                    return;
                }
                liveStates.putAll(states);
                liveNames.putAll(names);
                resultsAdapters[idx].clear();
                resultsAdapters[idx].addAll(labels);
                resultsAdapters[idx].notifyDataSetChanged();
                resultLists[idx].setVisibility(labels.isEmpty() ? View.GONE : View.VISIBLE);
                entityStatus.setVisibility(View.VISIBLE);
                entityStatus.setText(labels.isEmpty()
                    ? "No matches for “" + q + "”"
                    : labels.size() + " matches — tap to select");
            });
        }), 250);
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

    private void applyLive(int cellIndex) {
        List<String> cells = collectCells();
        DashboardConfig.saveCells(this, appWidgetId, cells);
        AppWidgetManager mgr = AppWidgetManager.getInstance(this);
        mgr.updateAppWidget(appWidgetId, DashboardWidget.build(this, appWidgetId));
        WidgetUpdater.request(this, DashboardWidget.class);
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
        WidgetApi.ensureCredentials(this);
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
                    if (!isControllableEntity(id)) continue;
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
                for (int i = 0; i < previews.length; i++) {
                    refreshPreview(i);
                    updateResultsList(i, textAt(i));
                }
            });
        });
    }

    @Override
    protected void onDestroy() {
        if (searchHandler != null) searchHandler.removeCallbacksAndMessages(null);
        super.onDestroy();
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

    /** Only offer controllable domains in widget pickers (no sensors/cameras/automations). */
    private static boolean isControllableEntity(String entityId) {
        int dot = entityId.indexOf('.');
        String domain = dot > 0 ? entityId.substring(0, dot) : "";
        switch (domain) {
            case "light":
            case "switch":
            case "cover":
            case "lock":
            case "fan":
            case "media_player":
            case "climate":
            case "button":
            case "input_button":
            case "scene":
            case "vacuum":
            case "humidifier":
            case "water_heater":
            case "remote":
            case "siren":
                return true;
            default:
                return false;
        }
    }

    private abstract static class SimpleWatcher implements TextWatcher {
        @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
        @Override public void onTextChanged(CharSequence s, int a, int b, int c) {}
        @Override public void afterTextChanged(Editable s) {}
    }
}
