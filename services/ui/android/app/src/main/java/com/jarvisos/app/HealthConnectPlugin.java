package com.jarvisos.app;

import android.content.Intent;
import android.net.Uri;
import androidx.activity.result.ActivityResult;
import androidx.activity.result.contract.ActivityResultContract;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.Collections;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

/**
 * Health Connect import: the connect/disconnect control on Health → Health,
 * mirroring StepCounter/PhoneUsage. Connecting asks Health Connect for read
 * access (sleep, resting heart rate, HRV, weight, exercise and distance, plus
 * background reading where the phone offers it), then syncs now and hourly
 * ({@link HealthConnectSyncWorker}) and whenever the app comes back to the
 * front. Reading and posting live in {@link HealthConnectSync}.
 */
@CapacitorPlugin(name = "HealthConnect")
public class HealthConnectPlugin extends Plugin {
    /** An app open syncs at most this often (the worker covers the rest). */
    private static final long RESUME_SYNC_MS = 15L * 60 * 1000;
    private static final ExecutorService IO = Executors.newSingleThreadExecutor();

    private static String availability(int status) {
        switch (status) {
            case 3: return "available";
            case 2: return "update_required";
            default: return "unavailable";
        }
    }

    private JSObject statusObject() {
        JSObject ret = new JSObject();
        int sdk = HealthConnectReader.sdkStatus(getContext());
        String availability = availability(sdk);
        ret.put("availability", availability);
        ret.put("connected", HealthConnectSync.enabled(getContext()));
        ret.put("owner", HealthConnectSync.prefs(getContext()).getString(HealthConnectSync.KEY_OWNER, null));
        JSArray granted = new JSArray();
        boolean background = false;
        if ("available".equals(availability)) {
            try {
                Set<String> perms = HealthConnectReader.grantedPermissions(getContext());
                for (String p : HealthConnectReader.dataPermissions()) {
                    if (perms.contains(p)) granted.put(shortName(p));
                }
                background = perms.contains(HealthConnectReader.BACKGROUND);
            } catch (Exception e) {
                ret.put("permissionError", e.getMessage());
            }
            ret.put("backgroundAvailable", HealthConnectReader.backgroundReadAvailable(getContext()));
        } else {
            ret.put("backgroundAvailable", false);
        }
        ret.put("granted", granted);
        ret.put("backgroundGranted", background);
        long last = HealthConnectSync.prefs(getContext()).getLong(HealthConnectSync.KEY_LAST_SYNC, 0L);
        ret.put("lastSync", last > 0 ? last : JSONObject.NULL);
        JSONObject result = HealthConnectSync.lastResult(getContext());
        ret.put("lastResult", result != null ? result : JSONObject.NULL);
        return ret;
    }

    /** "android.permission.health.READ_SLEEP" → "sleep". */
    private static String shortName(String permission) {
        String tail = permission.substring(permission.lastIndexOf('.') + 1);
        return tail.replace("READ_", "").toLowerCase(java.util.Locale.ROOT);
    }

    @PluginMethod
    public void status(PluginCall call) {
        IO.execute(() -> call.resolve(statusObject()));
    }

    /** Asks Health Connect for read access, then starts syncing for {@code username}. */
    @PluginMethod
    public void connect(PluginCall call) {
        String username = call.getString("username");
        if (username == null || username.trim().isEmpty()) {
            call.reject("username is required to connect Health Connect");
            return;
        }
        if (!HealthConnectReader.available(getContext())) {
            call.reject("Health Connect is not available on this phone");
            return;
        }
        IO.execute(() -> {
            try {
                ActivityResultContract<Set<String>, Set<String>> contract = HealthConnectReader.permissionContract();
                Intent intent = contract.createIntent(
                    getContext(), HealthConnectReader.requestedPermissions(getContext()));
                getActivity().runOnUiThread(() -> startActivityForResult(call, intent, "onPermissions"));
            } catch (Exception e) {
                call.reject(e.getMessage() != null ? e.getMessage() : "Could not open Health Connect", e);
            }
        });
    }

    @ActivityCallback
    private void onPermissions(PluginCall call, ActivityResult result) {
        if (call == null) return;
        String username = call.getString("username", "");
        IO.execute(() -> {
            Set<String> granted;
            try {
                granted = HealthConnectReader.grantedPermissions(getContext());
            } catch (Exception e) {
                granted = Collections.emptySet();
            }
            boolean any = false;
            for (String p : HealthConnectReader.dataPermissions()) {
                if (granted.contains(p)) any = true;
            }
            if (any) {
                HealthConnectSync.enable(getContext(), username);
                HealthConnectSync.run(getContext(), false);
            }
            call.resolve(statusObject());
        });
    }

    @PluginMethod
    public void syncNow(PluginCall call) {
        IO.execute(() -> {
            JSONObject result = HealthConnectSync.run(getContext(), false);
            JSObject ret = statusObject();
            ret.put("result", result);
            call.resolve(ret);
        });
    }

    /**
     * Stops importing. With {@code revoke: true} also withdraws Jarvis's Health
     * Connect access. Data already imported stays in the health store (it can
     * be deleted there like any reading).
     */
    @PluginMethod
    public void disconnect(PluginCall call) {
        boolean revoke = Boolean.TRUE.equals(call.getBoolean("revoke", false));
        IO.execute(() -> {
            HealthConnectSync.disable(getContext());
            if (revoke && HealthConnectReader.available(getContext())) {
                try {
                    HealthConnectReader.revokeAll(getContext());
                } catch (Exception e) {
                    // Access can still be withdrawn in Health Connect's own settings.
                }
            }
            call.resolve(statusObject());
        });
    }

    /** Health Connect's own screen (manage access and data), or its store page to install it. */
    @PluginMethod
    public void openHealthConnect(PluginCall call) {
        Intent intent;
        if (HealthConnectReader.sdkStatus(getContext()) == 3) {
            intent = new Intent("androidx.health.ACTION_HEALTH_CONNECT_SETTINGS");
        } else {
            intent = new Intent(Intent.ACTION_VIEW,
                Uri.parse("market://details?id=" + HealthConnectReader.PROVIDER + "&url=healthconnect%3A%2F%2Fonboarding"));
            intent.setPackage("com.android.vending");
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            getContext().startActivity(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject("Could not open Health Connect", e);
        }
    }

    @Override
    protected void handleOnResume() {
        super.handleOnResume();
        if (!HealthConnectSync.enabled(getContext())) return;
        long lastAttempt = HealthConnectSync.prefs(getContext()).getLong(HealthConnectSync.KEY_LAST_ATTEMPT, 0L);
        if (System.currentTimeMillis() - lastAttempt < RESUME_SYNC_MS) return;
        IO.execute(() -> HealthConnectSync.run(getContext(), false));
    }
}
