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
import android.widget.AutoCompleteTextView;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
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
    private AutoCompleteTextView fieldEntity;
    private EditText fieldLabel;
    private Spinner fieldService;
    private ProgressBar entityProgress;
    private TextView entityStatus;
    private final List<String> serviceKeys = new ArrayList<>();
    private final List<String> serviceLabels = new ArrayList<>();
    private String selectedIcon = MaterialIcons.defaultIcon();
    private final List<ImageView> iconViews = new ArrayList<>();

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
        Button btnSave = findViewById(R.id.btn_save);
        Button btnCancel = findViewById(R.id.btn_cancel);
        LinearLayout iconRow = findViewById(R.id.icon_row);

        ActionButtonConfig existing = ActionButtonConfig.load(this, appWidgetId);
        if (existing != null) {
            if (existing.label != null && !existing.label.isEmpty()) fieldLabel.setText(existing.label);
            fieldEntity.setText(existing.entityId);
            selectedIcon = existing.icon;
            rebuildServices(existing.service);
        } else {
            rebuildServices(null);
        }

        buildIconRow(iconRow);
        fieldEntity.addTextChangedListener(new SimpleWatcher() {
            @Override public void afterTextChanged(Editable s) {
                rebuildServices(null);
            }
        });
        loadEntities();

        btnSave.setOnClickListener(v -> save());
        btnCancel.setOnClickListener(v -> {
            setResult(Activity.RESULT_CANCELED);
            finish();
        });
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
        ArrayAdapter<String> adapter = new ArrayAdapter<>(
            this, android.R.layout.simple_spinner_dropdown_item, serviceLabels);
        fieldService.setAdapter(adapter);
        fieldService.setSelection(selected);
    }

    private void buildIconRow(LinearLayout row) {
        row.removeAllViews();
        iconViews.clear();
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
            final String iconId = name;
            final ImageView button = iv;
            highlightIcon(iv, name.equals(selectedIcon));
            iv.setOnClickListener(v -> {
                selectedIcon = iconId;
                for (ImageView other : iconViews) {
                    highlightIcon(other, other == button);
                }
            });
            iconViews.add(iv);
            row.addView(iv);
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
        if (WidgetApi.apiKey(this) == null) {
            entityStatus.setVisibility(View.VISIBLE);
            entityStatus.setText("Sign in to Jarvis OS to browse entities, or type an entity_id.");
            return;
        }
        entityProgress.setVisibility(View.VISIBLE);
        entityStatus.setVisibility(View.VISIBLE);
        entityStatus.setText("Loading entities…");
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
                    // Prefer controllable domains for action buttons
                    String domain = id.contains(".") ? id.substring(0, id.indexOf('.')) : "";
                    if (isControllableDomain(domain)) {
                        ids.add(id);
                        labels.add(friendly + "  (" + id + ")");
                    }
                }
            } catch (Exception e) {
                error[0] = e.getMessage() != null ? e.getMessage() : "Failed to load entities";
            }
            WidgetUpdater.onMain(() -> {
                entityProgress.setVisibility(View.GONE);
                if (error[0] != null) {
                    entityStatus.setText(error[0] + " — type entity_id manually");
                    return;
                }
                if (ids.isEmpty()) {
                    entityStatus.setText("No controllable entities found — type entity_id");
                    return;
                }
                entityStatus.setText(ids.size() + " entities");
                ArrayAdapter<String> adapter = new ArrayAdapter<>(
                    ConfigureActionButtonActivity.this,
                    android.R.layout.simple_dropdown_item_1line,
                    labels);
                fieldEntity.setAdapter(adapter);
                // Store id ↔ label mapping via tag on adapter positions
                fieldEntity.setOnItemClickListener((parent, view, position, id) -> {
                    // label format: "Friendly  (entity.id)" — extract id
                    String full = labels.get(position);
                    int open = full.lastIndexOf('(');
                    int close = full.lastIndexOf(')');
                    if (open >= 0 && close > open) {
                        fieldEntity.setText(full.substring(open + 1, close));
                    }
                });
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
        ActionButtonConfig cfg = new ActionButtonConfig(entity, service, label, selectedIcon);
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
