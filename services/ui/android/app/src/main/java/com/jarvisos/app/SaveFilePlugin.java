package com.jarvisos.app;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.util.Base64;

import androidx.activity.result.ActivityResult;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Saves a file the web app downloaded, where the person chooses.
 *
 * Android's WebView ignores an in-page download (an anchor with
 * {@code download} pointing at a {@code blob:} URL), so every Download button
 * in the app did nothing. The web side hands the bytes here instead, and
 * Android's own "Save to" picker (ACTION_CREATE_DOCUMENT) opens with the file
 * name filled in. Nothing is written until the person picks a place, and the
 * app never needs storage permission: the picker grants access to that one
 * file only.
 */
@CapacitorPlugin(name = "JarvisSaveFile")
public class SaveFilePlugin extends Plugin {

    private static final ExecutorService IO = Executors.newSingleThreadExecutor();

    @PluginMethod
    public void save(PluginCall call) {
        String data = call.getString("data");
        String filename = call.getString("filename");
        if (data == null || data.isEmpty()) {
            call.reject("No file data was given");
            return;
        }
        if (filename == null || filename.isEmpty()) {
            call.reject("No file name was given");
            return;
        }
        String mime = call.getString("mimeType");
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType(mime == null || mime.isEmpty() ? "application/octet-stream" : mime);
        intent.putExtra(Intent.EXTRA_TITLE, filename);
        startActivityForResult(call, intent, "onPicked");
    }

    @ActivityCallback
    private void onPicked(PluginCall call, ActivityResult result) {
        if (call == null) return;
        Intent picked = result.getData();
        Uri uri = picked == null ? null : picked.getData();
        if (result.getResultCode() != Activity.RESULT_OK || uri == null) {
            JSObject ret = new JSObject();
            ret.put("saved", false);
            call.resolve(ret);
            return;
        }
        String data = call.getString("data", "");
        IO.execute(() -> {
            try (OutputStream out = getContext().getContentResolver().openOutputStream(uri, "w")) {
                if (out == null) throw new IllegalStateException("The chosen place could not be opened");
                out.write(Base64.decode(data, Base64.DEFAULT));
                JSObject ret = new JSObject();
                ret.put("saved", true);
                call.resolve(ret);
            } catch (Exception e) {
                call.reject("The file could not be saved: " + e.getClass().getSimpleName()
                    + (e.getMessage() != null ? ": " + e.getMessage() : ""), e);
            }
        });
    }
}
