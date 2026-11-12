package com.jarvisos.app;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import org.json.JSONObject;

/**
 * Room detection for the web app's Settings: the beacon's state, the on/off
 * switch, the transmit power and Android's Nearby devices permission.
 *
 * The switch cannot be turned off on a child's phone (the guard's protected
 * phone): room detection is a family safety feature there, as location
 * sharing is, which children are not offered a way to pause.
 */
@CapacitorPlugin(
    name = "JarvisBleBeacon",
    permissions = {
        @Permission(alias = "bluetooth", strings = { Manifest.permission.BLUETOOTH_ADVERTISE })
    }
)
public class BleBeaconPlugin extends Plugin {

    @Override
    public void load() {
        BleBeacon.setListener(ctx -> notifyListeners("statusChange", statusOf(ctx)));
        BleBeacon.ensure(getContext());
    }

    static JSObject statusOf(Context c) {
        BeaconRules.Status s = BleBeacon.status(c);
        BeaconRules.TxPower power = BleBeacon.txPower(c);
        JSObject o = new JSObject();
        o.put("enabled", BleBeacon.enabled(c));
        o.put("state", s.state.id);
        o.put("reason", s.reason == null ? JSONObject.NULL : s.reason);
        o.put("uuid", BleBeacon.uuid(c));
        o.put("major", BleBeacon.major(c));
        o.put("minor", BleBeacon.minor(c));
        o.put("txPowerLevel", power.id);
        o.put("measuredPower", power.measuredPower);
        o.put("permissionGranted", BleBeacon.permissionGranted(c));
        o.put("canDisable", BeaconRules.canDisable(BleBeacon.protectedPhone(c)));
        Boolean registered = BleBeacon.hubRegistered(c);
        o.put("hubRegistered", registered == null ? JSONObject.NULL : registered);
        String detail = BleBeacon.hubDetail(c);
        o.put("hubDetail", detail == null ? JSONObject.NULL : detail);
        return o;
    }

    private boolean needsPermission() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !BleBeacon.permissionGranted(getContext());
    }

    @PluginMethod
    public void getStatus(PluginCall call) {
        BleBeacon.ensureIdentity(getContext());
        call.resolve(statusOf(getContext()));
    }

    @PluginMethod
    public void setEnabled(PluginCall call) {
        Boolean enabled = call.getBoolean("enabled");
        if (enabled == null) {
            call.reject("enabled is required");
            return;
        }
        Context c = getContext();
        if (!enabled && !BeaconRules.canDisable(BleBeacon.protectedPhone(c))) {
            call.reject("Room detection stays on on a child's phone; a parent manages it");
            return;
        }
        BleBeacon.setUserEnabled(c, enabled);
        BleBeacon.forceReport(c);
        if (enabled && needsPermission()) {
            requestPermissionForAlias("bluetooth", call, "permissionAnswered");
            return;
        }
        BleBeacon.ensure(c);
        call.resolve(statusOf(c));
    }

    @PluginMethod
    public void setTxPower(PluginCall call) {
        BeaconRules.TxPower power = BeaconRules.TxPower.fromId(call.getString("level"));
        if (power == null) {
            call.reject("level must be one of ultra_low, low, medium, high");
            return;
        }
        BleBeacon.setTxPower(getContext(), power);
        BleBeacon.forceReport(getContext());
        BleBeacon.ensure(getContext());
        call.resolve(statusOf(getContext()));
    }

    @PluginMethod
    public void requestPermission(PluginCall call) {
        if (!needsPermission()) {
            BleBeacon.ensure(getContext());
            call.resolve(statusOf(getContext()));
            return;
        }
        requestPermissionForAlias("bluetooth", call, "permissionAnswered");
    }

    @PermissionCallback
    private void permissionAnswered(PluginCall call) {
        // Granted or not, the state says which (a refusal reads "unavailable: permission not granted").
        BleBeacon.forceReport(getContext());
        BleBeacon.ensure(getContext());
        call.resolve(statusOf(getContext()));
    }

    @PluginMethod
    public void openSettings(PluginCall call) {
        Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.fromParts("package", getContext().getPackageName(), null));
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(i);
        call.resolve();
    }
}
