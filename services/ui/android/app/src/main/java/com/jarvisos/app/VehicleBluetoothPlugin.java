package com.jarvisos.app;

import android.Manifest;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import org.json.JSONObject;

/**
 * Wander's vehicle linking: the phone's paired Bluetooth devices, which of
 * them can be linked (a stable hardware address) and why not, which are
 * connected now, and Android's Nearby devices permission.
 */
@CapacitorPlugin(
    name = "JarvisVehicleBluetooth",
    permissions = {
        @Permission(alias = "bluetooth", strings = { Manifest.permission.BLUETOOTH_CONNECT })
    }
)
public class VehicleBluetoothPlugin extends Plugin {

    @Override
    public void load() {
        VehicleBluetooth.seed(getContext());
    }

    private JSObject status() {
        JSObject o = new JSObject();
        o.put("supported", VehicleBluetooth.adapter(getContext()) != null);
        o.put("permissionGranted", VehicleBluetooth.permissionGranted(getContext()));
        o.put("bluetoothOn", VehicleBluetooth.bluetoothOn(getContext()));
        return o;
    }

    private boolean needsPermission() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !VehicleBluetooth.permissionGranted(getContext());
    }

    @PluginMethod
    public void getStatus(PluginCall call) {
        call.resolve(status());
    }

    @PluginMethod
    public void requestPermission(PluginCall call) {
        if (!needsPermission()) {
            call.resolve(status());
            return;
        }
        requestPermissionForAlias("bluetooth", call, "permissionAnswered");
    }

    @PermissionCallback
    private void permissionAnswered(PluginCall call) {
        if (VehicleBluetooth.permissionGranted(getContext())) {
            VehicleBluetooth.seed(getContext());
            DeviceHealth.reportAsync(getContext());
        }
        call.resolve(status());
    }

    /** The paired devices, each with whether it can be linked and, if not, why. */
    @PluginMethod
    public void listDevices(PluginCall call) {
        JSObject out = status();
        JSArray devices = new JSArray();
        for (VehicleBluetooth.Bonded b : VehicleBluetooth.bonded(getContext())) {
            JSObject d = new JSObject();
            d.put("name", b.name == null ? "" : b.name);
            d.put("address", b.address);
            d.put("type", b.type == VehicleBluetoothRules.TYPE_LE ? "le"
                    : b.type == VehicleBluetoothRules.TYPE_DUAL ? "dual"
                    : b.type == VehicleBluetoothRules.TYPE_CLASSIC ? "classic" : "unknown");
            d.put("connected", b.connected);
            d.put("linkable", b.linkable.ok);
            d.put("kind", b.linkable.kind == null ? JSONObject.NULL : b.linkable.kind);
            d.put("reason", b.linkable.reason == null ? JSONObject.NULL : b.linkable.reason);
            d.put("linkedTo", b.linkedTo == null ? JSONObject.NULL : b.linkedTo);
            devices.put(d);
        }
        out.put("devices", devices);
        call.resolve(out);
    }

    /**
     * What an admin checks on a real phone (Wander's Bluetooth diagnostics): the
     * permission, Bluetooth, the linked devices connected now (the addresses a
     * fix would carry) and how many other devices are connected (a count only).
     */
    @PluginMethod
    public void getDiagnostics(PluginCall call) {
        JSObject o = status();
        o.put("apiLevel", Build.VERSION.SDK_INT);
        java.util.Set<String> connected = VehicleBluetooth.connectedNow(getContext());
        java.util.List<String> linkedNow = VehicleBluetooth.linkedConnectedNow(getContext());
        java.util.Map<String, String> names = VehicleBluetooth.linkedNames(getContext());
        JSArray vehicles = new JSArray();
        for (String a : linkedNow) {
            JSObject v = new JSObject();
            v.put("address", a);
            v.put("vehicle", names.containsKey(a) ? names.get(a) : JSONObject.NULL);
            vehicles.put(v);
        }
        o.put("linkedConnected", vehicles);
        o.put("otherConnectedCount", Math.max(0, connected.size() - linkedNow.size()));
        o.put("linkedCount", VehicleBluetooth.linked(getContext()).size());
        long at = VehicleBluetooth.linksAt(getContext());
        o.put("linksRefreshedAt", at > 0 ? at : JSONObject.NULL);
        call.resolve(o);
    }

    /** The web app read the vehicles (or changed a link): keep their linked addresses at once. */
    @PluginMethod
    public void setVehicles(PluginCall call) {
        JSObject answer = call.getData();
        VehicleBluetooth.remember(getContext(), answer);
        call.resolve();
    }

    @PluginMethod
    public void openSettings(PluginCall call) {
        Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.fromParts("package", getContext().getPackageName(), null));
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(i);
        call.resolve();
    }

    @PluginMethod
    public void openBluetoothSettings(PluginCall call) {
        Intent i = new Intent(Settings.ACTION_BLUETOOTH_SETTINGS);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(i);
        call.resolve();
    }
}
