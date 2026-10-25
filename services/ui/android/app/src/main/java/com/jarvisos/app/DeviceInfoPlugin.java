package com.jarvisos.app;

import android.content.res.Configuration;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONObject;

/**
 * What kind of device this is, as Android defines it.
 *
 * {@code Device.getInfo()} from @capacitor/device reports the model, the OS and
 * the platform but has no notion of form factor, so a tablet self-registered as
 * a phone. Android's own rule is the smallest screen width: 600dp and up is a
 * tablet (https://developer.android.com/guide/topics/large-screens). The raw
 * signal is reported as-is; the web side decides the word to show.
 */
@CapacitorPlugin(name = "JarvisDeviceInfo")
public class DeviceInfoPlugin extends Plugin {

    /** "tablet" or "phone" from the screen width, or null when unreadable. */
    @PluginMethod
    public void getFormFactor(PluginCall call) {
        JSObject ret = new JSObject();
        Configuration config = getContext().getResources().getConfiguration();
        int widthDp = config.smallestScreenWidthDp;
        // A missing width (0) means the platform did not report one; return
        // null rather than guess a form factor from the model name.
        if (widthDp <= 0) {
            ret.put("formFactor", JSONObject.NULL);
        } else {
            ret.put("formFactor", widthDp >= 600 ? "tablet" : "phone");
        }
        call.resolve(ret);
    }
}
