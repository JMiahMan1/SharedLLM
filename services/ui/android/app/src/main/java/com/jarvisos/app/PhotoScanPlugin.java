package com.jarvisos.app;

import android.Manifest;
import android.content.Context;
import android.os.Build;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Photos and videos: the permission check and prompt for reading the phone's
 * gallery, and {@code scanNow}, which runs one scanning pass
 * ({@link PhotoScan}) off the main thread. The {@link PhotoScanWorker} runs
 * the same pass while the app is closed. Scanning is only active on a
 * protected child's phone with photo scanning on; the permission is still
 * what Android requires before any photo can be read.
 */
@CapacitorPlugin(name = "PhotoScan")
public class PhotoScanPlugin extends Plugin {

    private static final int ACCESS_REQUEST_CODE = 0x10C1;
    /** The call waiting on the permission dialog, resolved from its result. */
    private PluginCall pendingAccessCall;

    @PluginMethod
    public void hasAccess(PluginCall call) {
        resolveAccess(call);
    }

    @PluginMethod
    public void requestAccess(PluginCall call) {
        List<String> needed = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33) {
            if (!PhotoScan.hasImageAccess(bridge.getContext())) {
                needed.add("android.permission.READ_MEDIA_IMAGES");
                needed.add("android.permission.READ_MEDIA_VIDEO");
                if (Build.VERSION.SDK_INT >= 34) {
                    needed.add("android.permission.READ_MEDIA_VISUAL_USER_SELECTED");
                }
            } else if (!PhotoScan.hasVideoAccess(bridge.getContext())) {
                needed.add("android.permission.READ_MEDIA_VIDEO");
            }
        } else if (!PhotoScan.hasMediaAccess(bridge.getContext())) {
            needed.add(Manifest.permission.READ_EXTERNAL_STORAGE);
        }
        if (!needed.isEmpty() && getActivity() != null) {
            // The answer arrives after the dialog closes; resolving here would
            // report the state from before it.
            pendingAccessCall = call;
            getActivity().requestPermissions(needed.toArray(new String[0]), ACCESS_REQUEST_CODE);
            return;
        }
        resolveAccess(call);
    }

    @Override
    public void handleRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.handleRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != ACCESS_REQUEST_CODE) return;
        PluginCall call = pendingAccessCall;
        pendingAccessCall = null;
        if (call == null || call.isReleased()) return;
        resolveAccess(call);
    }

    /** One scanning pass now; resolves {ok, scanned, uploaded}. */
    @PluginMethod
    public void scanNow(PluginCall call) {
        Context app = bridge.getContext().getApplicationContext();
        new Thread(() -> {
            JSONObject result = PhotoScan.run(app);
            JSObject ret = new JSObject();
            ret.put("ok", result.optBoolean("ok", false));
            ret.put("scanned", result.optInt("scanned", 0));
            ret.put("uploaded", result.optInt("uploaded", 0));
            call.resolve(ret);
        }, "jarvis-photo-scan").start();
    }

    private void resolveAccess(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("images", PhotoScan.hasImageAccess(bridge.getContext()));
        ret.put("videos", PhotoScan.hasVideoAccess(bridge.getContext()));
        call.resolve(ret);
    }
}
