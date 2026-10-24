package com.jarvisos.app;

import android.app.admin.DevicePolicyManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.provider.Settings;
import android.text.TextUtils;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Setting up Jarvis's protection on a child's phone, from the app: whether
 * device admin and the screen guard are on, and the Settings screens a parent
 * uses to switch them on. Android only lets the person holding the phone grant
 * either one; this only opens the right page.
 */
@CapacitorPlugin(name = "JarvisGuard")
public class GuardPlugin extends Plugin {

    private ComponentName admin() {
        return new ComponentName(getContext(), GuardAdminReceiver.class);
    }

    static boolean guardEnabled(Context context) {
        String enabled = Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        if (TextUtils.isEmpty(enabled)) return false;
        String mine = new ComponentName(context, GuardService.class).flattenToString();
        for (String service : enabled.split(":")) {
            if (service.equalsIgnoreCase(mine)) return true;
        }
        return false;
    }

    @PluginMethod
    public void status(PluginCall call) {
        DevicePolicyManager dpm = (DevicePolicyManager) getContext().getSystemService(Context.DEVICE_POLICY_SERVICE);
        JSObject out = new JSObject();
        out.put("adminActive", dpm != null && dpm.isAdminActive(admin()));
        out.put("deviceOwner", dpm != null && dpm.isDeviceOwnerApp(getContext().getPackageName()));
        out.put("guardEnabled", guardEnabled(getContext()));
        out.put("webFilterAllowed", android.net.VpnService.prepare(getContext()) == null);
        out.put("webFilterActive", WebFilterService.active);
        call.resolve(out);
    }

    /** Android's one-time "allow this VPN" prompt for the web filter; the guard starts it after. */
    @PluginMethod
    public void allowWebFilter(PluginCall call) {
        Intent consent = android.net.VpnService.prepare(getContext());
        if (consent != null) {
            getActivity().startActivity(consent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        }
        call.resolve();
    }

    @PluginMethod
    public void enableAdmin(PluginCall call) {
        Intent intent = new Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN)
                .putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, admin())
                .putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                        "Keeps Jarvis on this phone. Removing it will need a parent.")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        getActivity().startActivity(intent);
        call.resolve();
    }

    @PluginMethod
    public void openGuardSettings(PluginCall call) {
        getActivity().startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        call.resolve();
    }

    /** App info, where Android 13+ hides "Allow restricted settings" for a sideloaded app. */
    @PluginMethod
    public void openAppInfo(PluginCall call) {
        getActivity().startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.parse("package:" + getContext().getPackageName()))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        call.resolve();
    }
}
