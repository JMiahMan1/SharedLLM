package com.jarvisos.app.widgets;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.Intent;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.ProgressBar;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;
import com.jarvisos.app.R;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.json.JSONObject;

/** Launcher configure activity for ActionButtonWidget (entity + service + Material icon). */
public class ConfigureActionButtonActivity extends Activity {
    private int appWidgetId = AppWidgetManager.INVALID_APPWIDGET_ID;
    private EditText fieldEntity;
    private EditText fieldLabel;
    private Spinner fieldService;
    private ProgressBar entityProgress;
    private TextView entityStatus;
    private TextView entityPreview;
    private TextView iconName;
    private ListView entityResults;
    private final List<String> serviceKeys = new ArrayList<>();
    private final List<String> serviceLabels = new ArrayList<>();
    private String selectedIcon = MaterialIcons.defaultIcon();
    private int selectedIconBg = 0;
    private final List<ImageView> iconViews = new ArrayList<>();
    private final List<ImageView> bgSwatchViews = new ArrayList<>();
    private static final int[] ICON_BG_CHOICES = {
        0,                 // transparent
        0x331E293B,        // slate 20%
        0x660F172A,        // navy 40%
        0x4D4ADE80,        // green 30%
        0x4D863BFF,        // purple 30%
        0x4D38BDF8,        // sky 30%
    };
    private static final String[] ICON_BG_LABELS = {
        "None", "Slate", "Navy", "Green", "Purple", "Sky"
    };
    private final List<String> entityIds = new ArrayList<>();
    private final List<String> entityLabels = new ArrayList<>();
    private ArrayAdapter<String> resultsAdapter;
    private int loadGeneration;
    private int previewGeneration;
    private int searchGeneration;
    private android.os.Handler previewHandler;
    private android.os.Handler searchHandler;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_configure_action_button);

        appWidgetId = getIntent().getIntExtra(
            AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        if (appWidgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            finish();
            return;
        }

        fieldLabel = findViewById(R.id.field_label);
        fieldEntity = findViewById(R.id.field_entity);
        fieldService = findViewById(R.id.field_service);
        entityProgress = findViewById(R.id.entity_progress);
        entityStatus = findViewById(R.id.entity_status);
        entityPreview = findViewById(R.id.entity_preview);
        entityResults = findViewById(R.id.entity_results);
        iconName = findViewById(R.id.icon_name);
        previewHandler = new android.os.Handler(getMainLooper());
        searchHandler = new android.os.Handler(getMainLooper());
        Button btnSave = findViewById(R.id.btn_save);
        Button btnCancel = findViewById(R.id.btn_cancel);
        LinearLayout iconRow = findViewById(R.id.icon_row);

        resultsAdapter = makeDarkAdapter();
        entityResults.setAdapter(resultsAdapter);
        entityResults.setOnItemClickListener((parent, view, position, id) -> {
            String full = resultsAdapter.getItem(position);
            if (full != null) pickEntity(full);
        });

        ActionButtonConfig existing = ActionButtonConfig.load(this, appWidgetId);
        if (existing != null) {
            if (existing.label != null && !existing.label.isEmpty()) fieldLabel.setText(existing.label);
            fieldEntity.setText(existing.entityId);
            selectedIcon = existing.icon;
            selectedIconBg = existing.iconBg;
            rebuildServices(existing.service);
        } else {
            rebuildServices(null);
        }

        buildIconRow(iconRow);
        buildIconBgRow();
        fieldEntity.addTextChangedListener(new SimpleWatcher() {
            @Override public void afterTextChanged(Editable s) {
                rebuildServices(null);
                String q = s != null ? s.toString().trim() : "";
                scheduleLivePreview(q);
                updateResultsList(q);
                scheduleServerSearch(q);
            }
        });
        // Focus shows the match list once entities are loaded
        fieldEntity.setOnFocusChangeListener((v, hasFocus) -> {
            if (hasFocus) {
                String q = fieldEntity.getText() != null ? fieldEntity.getText().toString().trim() : "";
                updateResultsList(q);
            }
        });
        loadEntities();

        btnSave.setOnClickListener(v -> save());
        btnCancel.setOnClickListener(v -> {
            setResult(Activity.RESULT_CANCELED);
            finish();
        });
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

    /** Filter the always-visible match list under the entity field. */
    private void updateResultsList(String query) {
        if (entityResults == null || resultsAdapter == null) return;
        if (entityLabels.isEmpty()) {
            entityResults.setVisibility(View.GONE);
            return;
        }
        String q = query == null ? "" : query.toLowerCase();
        List<String> matches = new ArrayList<>();
        // Empty query → show first matches so the list is discoverable
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
        resultsAdapter.clear();
        resultsAdapter.addAll(matches);
        resultsAdapter.notifyDataSetChanged();
        entityResults.setVisibility(matches.isEmpty() ? View.GONE : View.VISIBLE);
    }

    private void pickEntity(String full) {
        int open = full.lastIndexOf('(');
        int close = full.lastIndexOf(')');
        if (open >= 0 && close > open) {
            fieldEntity.setText(full.substring(open + 1, close));
            fieldEntity.setSelection(fieldEntity.getText().length());
        }
        rebuildServices(null);
        scheduleLivePreview(fieldEntity.getText() != null
            ? fieldEntity.getText().toString().trim() : "");
        if (entityResults != null) entityResults.setVisibility(View.GONE);
    }

    @Override
    protected void onDestroy() {
        if (previewHandler != null) previewHandler.removeCallbacksAndMessages(null);
        if (searchHandler != null) searchHandler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    /**
     * Debounced server-side search so typing uses the current app credentials
     * and always reflects live HA state (not just a one-shot local cache).
     */
    private void scheduleServerSearch(String query) {
        if (searchHandler == null) return;
        final String q = query == null ? "" : query;
        if (q.isEmpty()) {
            updateResultsList("");
            return;
        }
        final int gen = ++searchGeneration;
        searchHandler.removeCallbacksAndMessages(null);
        searchHandler.postDelayed(() -> WidgetUpdater.onBackground(() -> {
            final List<String> ids = new ArrayList<>();
            final List<String> labels = new ArrayList<>();
            final String[] error = new String[1];
            try {
                WidgetApi.ensureCredentials(this);
                if (WidgetApi.apiKey(this) == null) {
                    error[0] = "Sign in to Jarvis OS to search entities.";
                } else {
                    JSONObject states = WidgetApi.searchEntities(this, q, 40);
                    java.util.Iterator<String> it = states.keys();
                    while (it.hasNext()) {
                        String id = it.next();
                        JSONObject e = states.optJSONObject(id);
                        String friendly = e != null ? WidgetApi.friendlyName(e) : id;
                        String state = e != null ? e.optString("state", "") : "";
                        String domain = id.contains(".") ? id.substring(0, id.indexOf('.')) : "";
                        if (isControllableDomain(domain)) {
                            ids.add(id);
                            labels.add(friendly + (state.isEmpty() ? "" : " · " + state) + "  (" + id + ")");
                        }
                    }
                }
            } catch (Exception e) {
                error[0] = e.getMessage() != null ? e.getMessage() : "Search failed";
            }
            WidgetUpdater.onMain(() -> {
                if (gen != searchGeneration || isFinishing() || entityResults == null) return;
                entityProgress.setVisibility(View.GONE);
                if (error[0] != null) {
                    entityStatus.setVisibility(View.VISIBLE);
                    entityStatus.setText(error[0]);
                    // Fall back to local cache filter
                    updateResultsList(q);
                    return;
                }
                resultsAdapter.clear();
                resultsAdapter.addAll(labels);
                resultsAdapter.notifyDataSetChanged();
                entityResults.setVisibility(labels.isEmpty() ? View.GONE : View.VISIBLE);
                entityStatus.setVisibility(View.VISIBLE);
                entityStatus.setText(labels.isEmpty()
                    ? "No matches for “" + q + "”"
                    : labels.size() + " matches — tap to select");
            });
        }), 250);
    }

    /** Debounced live state lookup for the current entity_id text. */
    private void scheduleLivePreview(String entityId) {
        if (previewHandler == null) return;
        WidgetApi.ensureCredentials(this);
        if (entityId == null || !entityId.contains(".") || WidgetApi.apiKey(this) == null) {
            if (entityPreview != null) entityPreview.setVisibility(View.GONE);
            return;
        }
        final String id = entityId;
        final int gen = ++previewGeneration;
        previewHandler.postDelayed(() -> WidgetUpdater.onBackground(() -> {
            String line = null;
            try {
                List<String> only = java.util.Collections.singletonList(id);
                JSONObject states = WidgetApi.entityStates(this, only);
                JSONObject e = states.optJSONObject(id);
                if (e != null) {
                    line = WidgetApi.friendlyName(e) + " · " + e.optString("state", "?");
                }
            } catch (Exception ignored) {
            }
            final String text = line;
            WidgetUpdater.onMain(() -> {
                if (gen != previewGeneration || isFinishing() || entityPreview == null) return;
                if (text != null) {
                    entityPreview.setVisibility(View.VISIBLE);
                    entityPreview.setText(text);
                } else {
                    entityPreview.setVisibility(View.GONE);
                }
            });
        }), 250);
    }

    private void rebuildServices(String preferred) {
        String entity = fieldEntity.getText() != null ? fieldEntity.getText().toString().trim() : "";
        String domain = entity.contains(".") ? entity.substring(0, entity.indexOf('.')) : "";
        Map<String, String> services = ActionButtonConfig.servicesForDomain(domain);
        serviceKeys.clear();
        serviceLabels.clear();
        int selected = 0;
        int i = 0;
        for (Map.Entry<String, String> e : services.entrySet()) {
            serviceKeys.add(e.getKey());
            serviceLabels.add(e.getValue());
            if (preferred != null && preferred.equals(e.getKey())) selected = i;
            i++;
        }
        ArrayAdapter<String> adapter = new ArrayAdapter<String>(
            this, android.R.layout.simple_spinner_dropdown_item, serviceLabels) {
            @Override public android.view.View getView(int position, android.view.View convertView, android.view.ViewGroup parent) {
                android.view.View v = super.getView(position, convertView, parent);
                if (v instanceof TextView) ((TextView) v).setTextColor(0xFFF1F5F9);
                return v;
            }
            @Override public android.view.View getDropDownView(int position, android.view.View convertView, android.view.ViewGroup parent) {
                android.view.View v = super.getDropDownView(position, convertView, parent);
                if (v instanceof TextView) ((TextView) v).setTextColor(0xFFF1F5F9);
                return v;
            }
        };
        fieldService.setAdapter(adapter);
        fieldService.setSelection(selected);
    }

    private void buildIconRow(LinearLayout row) {
        row.removeAllViews();
        iconViews.clear();
        updateIconNameLabel();
        int cell = MaterialIcons.dp(getResources(), 48);
        int pad = MaterialIcons.dp(getResources(), 6);
        for (String name : MaterialIcons.names()) {
            ImageView iv = new ImageView(this);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(cell, cell);
            lp.setMargins(pad, pad, pad, pad);
            iv.setLayoutParams(lp);
            iv.setScaleType(ImageView.ScaleType.FIT_CENTER);
            iv.setImageResource(MaterialIcons.drawableRes(name));
            iv.setContentDescription(name);
            iv.setTooltipText(name);
            final String iconId = name;
            final ImageView button = iv;
            highlightIcon(iv, name.equals(selectedIcon));
            iv.setOnClickListener(v -> {
                selectedIcon = iconId;
                for (ImageView other : iconViews) {
                    highlightIcon(other, other == button);
                }
                updateIconNameLabel();
            });
            iconViews.add(iv);
            row.addView(iv);
        }
    }

    /** Optional chip color behind the widget icon (0 = transparent). */
    private void buildIconBgRow() {
        LinearLayout row = findViewById(R.id.icon_bg_row);
        if (row == null) return;
        row.removeAllViews();
        bgSwatchViews.clear();
        int cell = MaterialIcons.dp(getResources(), 40);
        int pad = MaterialIcons.dp(getResources(), 6);
        TextView label = new TextView(this);
        label.setText("Icon background:");
        label.setTextColor(0xFFCBD5E1);
        label.setTextSize(12);
        LinearLayout.LayoutParams llp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        llp.gravity = android.view.Gravity.CENTER_VERTICAL;
        label.setLayoutParams(llp);
        row.addView(label);
        for (int i = 0; i < ICON_BG_CHOICES.length; i++) {
            final int color = ICON_BG_CHOICES[i];
            ImageView sw = new ImageView(this);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(cell, cell);
            lp.setMargins(pad, pad, pad, pad);
            sw.setLayoutParams(lp);
            sw.setContentDescription(ICON_BG_LABELS[i]);
            sw.setTooltipText(ICON_BG_LABELS[i]);
            android.graphics.drawable.GradientDrawable bg = new android.graphics.drawable.GradientDrawable();
            bg.setShape(android.graphics.drawable.GradientDrawable.OVAL);
            bg.setColor(color == 0 ? 0x00000000 : color);
            bg.setStroke(MaterialIcons.dp(getResources(), 2),
                color == selectedIconBg ? 0xFF4ADE80 : 0x475569);
            sw.setBackground(bg);
            final boolean selected = color == selectedIconBg;
            if (selected) sw.setAlpha(1f); else sw.setAlpha(0.75f);
            sw.setOnClickListener(v -> {
                selectedIconBg = color;
                buildIconBgRow();
                updateIconNameLabel();
            });
            bgSwatchViews.add(sw);
            row.addView(sw);
        }
    }

    private void updateIconNameLabel() {
        if (iconName != null) {
            String bg = selectedIconBg == 0 ? "bg none" : "bg set";
            iconName.setText("Selected: " + selectedIcon + " · " + bg);
        }
    }

    private void highlightIcon(ImageView iv, boolean selected) {
        GradientDrawable bg = new GradientDrawable();
        bg.setShape(GradientDrawable.RECTANGLE);
        bg.setCornerRadius(MaterialIcons.dp(getResources(), 10));
        if (selected) {
            bg.setColor(0x334ADE80);
            bg.setStroke(MaterialIcons.dp(getResources(), 2), 0xFF4ADE80);
            iv.setColorFilter(0xFF4ADE80);
        } else {
            bg.setColor(0x1A1E293B);
            bg.setStroke(1, 0x33475569);
            iv.clearColorFilter();
            iv.setAlpha(0.85f);
        }
        iv.setBackground(bg);
        if (selected) iv.setAlpha(1f);
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
            final String[] error = new String[1];
            try {
                JSONObject states = WidgetApi.entityStates(this, null);
                java.util.Iterator<String> it = states.keys();
                while (it.hasNext()) {
                    String id = it.next();
                    JSONObject e = states.optJSONObject(id);
                    String friendly = e != null ? WidgetApi.friendlyName(e) : id;
                    String state = e != null ? e.optString("state", "") : "";
                    String domain = id.contains(".") ? id.substring(0, id.indexOf('.')) : "";
                    if (isControllableDomain(domain)) {
                        ids.add(id);
                        labels.add(friendly + (state.isEmpty() ? "" : " · " + state) + "  (" + id + ")");
                    }
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
                    entityStatus.setText("No controllable entities found — type entity_id");
                    return;
                }
                entityStatus.setText(ids.size() + " entities — type to search");
                entityIds.clear();
                entityIds.addAll(ids);
                entityLabels.clear();
                entityLabels.addAll(labels);
                CharSequence now = fieldEntity.getText();
                updateResultsList(now != null ? now.toString().trim() : "");
                CharSequence cur = fieldEntity.getText();
                if (cur != null && cur.length() > 0) {
                    scheduleLivePreview(cur.toString().trim());
                }
            });
        });
    }

    private static boolean isControllableDomain(String domain) {
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

    private void save() {
        String entity = fieldEntity.getText() != null ? fieldEntity.getText().toString().trim() : "";
        if (entity.isEmpty() || !entity.contains(".")) {
            Toast.makeText(this, "Enter an entity_id (e.g. switch.garage_door_2)", Toast.LENGTH_SHORT).show();
            return;
        }
        String service = serviceKeys.isEmpty() ? "turn_on" : serviceKeys.get(Math.max(0, fieldService.getSelectedItemPosition()));
        CharSequence labelCs = fieldLabel.getText();
        String label = labelCs != null ? labelCs.toString().trim() : "";
        ActionButtonConfig cfg = new ActionButtonConfig(entity, service, label, selectedIcon, selectedIconBg);
        ActionButtonConfig.save(this, appWidgetId, cfg);

        AppWidgetManager mgr = AppWidgetManager.getInstance(this);
        mgr.updateAppWidget(appWidgetId, ActionButtonWidget.build(this, appWidgetId));
        WidgetUpdater.request(this, ActionButtonWidget.class);

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
