package com.jarvisos.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.util.Base64;
import android.util.Log;
import android.widget.Toast;

import androidx.activity.result.ActivityResult;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Saves a file the web app downloaded, where the person chooses.
 *
 * Android's WebView ignores an in-page download (an anchor with
 * {@code download} pointing at a {@code blob:} URL), so the web side hands the
 * bytes here instead, and Android's own "Save to" picker (ACTION_CREATE_DOCUMENT)
 * opens with the file name filled in. Nothing is written where the person can
 * see it until they pick a place, and the app never needs storage permission:
 * the picker grants access to that one file only.
 *
 * The bytes travel in pieces: {@code begin} stages an empty file in the app's
 * cache, {@code append} adds base64 pieces to it, and {@code save} opens the
 * picker and copies the staged file to the place chosen. The staged file is
 * deleted on every path. {@code save} with {@code data} (the web bundles from
 * before staging) stages the bytes itself first.
 *
 * The picker is started through this plugin's own launcher, not Capacitor's
 * {@code startActivityForResult(call, ...)}. That one keeps the call in the
 * bridge, where (1) the activity's saved state copies the call's whole payload
 * when the picker covers the app, which crashed the app with
 * TransactionTooLargeException for a file of a couple of megabytes, and (2)
 * every answer goes to the most recent call, so a second save took the first
 * one's answer (writing its bytes into the first file) and the first promise
 * never settled.
 */
@CapacitorPlugin(name = "JarvisSaveFile")
public class SaveFilePlugin extends Plugin {

    private static final String TAG = "JarvisSaveFile";
    private static final String PREFS = "jarvis_save_file";
    private static final String PREF_ID = "pending_id";
    private static final String PREF_NAME = "pending_name";
    private static final String DIR = "save-file";
    /** How long after the app is back in front an answer may still arrive. */
    private static final long ANSWER_GRACE_MS = 1500;

    private static final ExecutorService IO = Executors.newSingleThreadExecutor();

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Object lock = new Object();

    private ActivityResultLauncher<Intent> picker;

    // The one save waiting for the picker (guarded by lock).
    private PluginCall waiting;
    private String waitingId;
    private String waitingName;
    private boolean pickerOpened;
    private boolean pausedSincePickerOpened;

    @Override
    public void load() {
        // Registered while the activity is being created, so Android can hand
        // this launcher the picker's answer even after recreating the activity.
        picker = getBridge().registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(),
            this::onPicked
        );
        IO.execute(this::removeStale);
    }

    /** Capacitor must never copy a save's payload into the activity's saved state. */
    @Override
    protected Bundle saveInstanceState() {
        return null;
    }

    // --- Staging ------------------------------------------------------------------

    @PluginMethod
    public void begin(PluginCall call) {
        String id = SaveFileRules.newId();
        try {
            File part = partFile(id);
            if (!part.createNewFile()) throw new IllegalStateException("The staging file already exists");
            JSObject ret = new JSObject();
            ret.put("id", id);
            call.resolve(ret);
        } catch (Exception e) {
            call.reject(describe("The file could not be prepared", e), e);
        }
    }

    @PluginMethod
    public void append(PluginCall call) {
        String id = call.getString("id");
        String data = call.getString("data");
        if (!SaveFileRules.validId(id) || !partFile(id).isFile()) {
            call.reject("Unknown file");
            return;
        }
        File part = partFile(id);
        try (OutputStream out = new FileOutputStream(part, true)) {
            if (data != null && !data.isEmpty()) out.write(Base64.decode(data, Base64.DEFAULT));
            call.resolve();
        } catch (Exception e) {
            //noinspection ResultOfMethodCallIgnored
            part.delete();
            call.reject(describe("The file could not be prepared", e), e);
        }
    }

    @PluginMethod
    public void discard(PluginCall call) {
        String id = call.getString("id");
        synchronized (lock) {
            if (SaveFileRules.validId(id) && !id.equals(waitingId)) {
                //noinspection ResultOfMethodCallIgnored
                partFile(id).delete();
            }
        }
        call.resolve();
    }

    // --- Saving -------------------------------------------------------------------

    @PluginMethod
    public void save(PluginCall call) {
        String filename = call.getString("filename");
        if (filename == null || filename.isEmpty()) {
            call.reject("No file name was given");
            return;
        }
        String id = call.getString("id");
        boolean stagedHere = id == null;
        if (stagedHere) {
            // A web bundle from before staging sends the whole file here.
            String data = call.getString("data");
            if (data == null || data.isEmpty()) {
                call.reject("No file data was given");
                return;
            }
            id = SaveFileRules.newId();
            try (OutputStream out = new FileOutputStream(partFile(id))) {
                out.write(Base64.decode(data, Base64.DEFAULT));
            } catch (Exception e) {
                //noinspection ResultOfMethodCallIgnored
                partFile(id).delete();
                call.reject(describe("The file could not be prepared", e), e);
                return;
            }
        } else if (!SaveFileRules.validId(id) || !partFile(id).isFile()) {
            call.reject("Unknown file");
            return;
        }

        synchronized (lock) {
            if (waiting != null) {
                if (stagedHere) {
                    //noinspection ResultOfMethodCallIgnored
                    partFile(id).delete();
                }
                call.reject("Another file is still waiting for a place to save it", "BUSY");
                return;
            }
            waiting = call;
            waitingId = id;
            waitingName = filename;
            pickerOpened = false;
            pausedSincePickerOpened = false;
            remember(id, filename);
        }

        String mime = call.getString("mimeType");
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType(mime == null || mime.isEmpty() ? "application/octet-stream" : mime);
        intent.putExtra(Intent.EXTRA_TITLE, filename);
        final String launchedId = id;
        main.post(() -> {
            try {
                picker.launch(intent);
                synchronized (lock) {
                    if (launchedId.equals(waitingId)) pickerOpened = true;
                }
            } catch (ActivityNotFoundException e) {
                fail(launchedId, "This phone has no app to save files with", e);
            } catch (Exception e) {
                fail(launchedId, describe("The save screen could not be opened", e), e);
            }
        });
    }

    @Override
    protected void handleOnPause() {
        synchronized (lock) {
            if (pickerOpened) pausedSincePickerOpened = true;
        }
    }

    @Override
    protected void handleOnResume() {
        // Android delivers the picker's answer before the app is back in
        // front; give it a moment more, then settle a call that got none.
        main.postDelayed(() -> {
            PluginCall lost;
            String lostId;
            synchronized (lock) {
                if (!SaveFileRules.answerLost(waiting != null, pickerOpened, pausedSincePickerOpened)) return;
                lost = waiting;
                lostId = waitingId;
                clearWaiting();
            }
            Log.w(TAG, "The save screen closed without an answer");
            forget();
            //noinspection ResultOfMethodCallIgnored
            partFile(lostId).delete();
            JSObject ret = new JSObject();
            ret.put("saved", false);
            ret.put("reason", "no-answer");
            lost.resolve(ret);
        }, ANSWER_GRACE_MS);
    }

    private void onPicked(ActivityResult result) {
        Intent picked = result.getData();
        Uri uri = picked == null ? null : picked.getData();
        boolean ok = result.getResultCode() == Activity.RESULT_OK;

        PluginCall call;
        String id;
        String name;
        synchronized (lock) {
            call = waiting;
            id = waitingId;
            name = waitingName;
            clearWaiting();
        }
        if (call == null) {
            // Android ended the app while the picker was up: the staged file
            // may have survived in the cache even though the call did not.
            SharedPreferences prefs = prefs();
            id = prefs.getString(PREF_ID, null);
            name = prefs.getString(PREF_NAME, null);
        }
        forget();
        File part = SaveFileRules.validId(id) ? partFile(id) : null;

        SaveFileRules.Outcome outcome =
            SaveFileRules.onResult(call != null, part != null && part.isFile(), ok, uri != null);
        switch (outcome) {
            case WRITE:
                writeTo(uri, part, call, name);
                break;
            case RECOVER:
                writeTo(uri, part, null, name);
                break;
            case STAGED_FILE_MISSING:
                deleteQuietly(uri);
                call.reject("The file to save was lost before it could be written");
                break;
            case CANCELLED: {
                if (part != null) {
                    //noinspection ResultOfMethodCallIgnored
                    part.delete();
                }
                JSObject ret = new JSObject();
                ret.put("saved", false);
                call.resolve(ret);
                break;
            }
            case DISCARD_ORPHAN:
                //noinspection ResultOfMethodCallIgnored
                part.delete();
                break;
            case NOTHING:
            default:
                break;
        }
    }

    /** Copies the staged file to the chosen place off the main thread, then deletes it. */
    private void writeTo(Uri uri, File part, PluginCall call, String name) {
        Context context = getContext().getApplicationContext();
        IO.execute(() -> {
            try {
                long bytes;
                try (InputStream in = new FileInputStream(part); OutputStream out = openTruncating(context, uri)) {
                    bytes = SaveFileRules.copy(in, out);
                }
                if (call != null) {
                    JSObject ret = new JSObject();
                    ret.put("saved", true);
                    ret.put("bytes", bytes);
                    call.resolve(ret);
                } else {
                    toast(context, "Saved " + (name != null ? name : "the file"));
                }
            } catch (Exception e) {
                Log.w(TAG, "Saving failed", e);
                deleteQuietly(uri);
                String message = describe("The file could not be saved", e);
                if (call != null) call.reject(message, e);
                else toast(context, message);
            } finally {
                //noinspection ResultOfMethodCallIgnored
                part.delete();
            }
        });
    }

    // --- Helpers ------------------------------------------------------------------

    private static OutputStream openTruncating(Context context, Uri uri) throws FileNotFoundException {
        OutputStream out;
        try {
            out = context.getContentResolver().openOutputStream(uri, "wt");
        } catch (IllegalArgumentException | FileNotFoundException e) {
            // A provider without the truncating mode; the picker made a new file anyway.
            out = context.getContentResolver().openOutputStream(uri, "w");
        }
        if (out == null) throw new FileNotFoundException("The chosen place could not be opened");
        return out;
    }

    /** A half-written or empty file in the chosen place helps nobody. */
    private void deleteQuietly(Uri uri) {
        if (uri == null) return;
        try {
            DocumentsContract.deleteDocument(getContext().getContentResolver(), uri);
        } catch (Exception ignored) {
            // The person can still delete it by hand.
        }
    }

    private void fail(String id, String message, Exception e) {
        PluginCall call;
        synchronized (lock) {
            if (!id.equals(waitingId)) return;
            call = waiting;
            clearWaiting();
        }
        forget();
        //noinspection ResultOfMethodCallIgnored
        partFile(id).delete();
        call.reject(message, e);
    }

    private void clearWaiting() {
        waiting = null;
        waitingId = null;
        waitingName = null;
        pickerOpened = false;
        pausedSincePickerOpened = false;
    }

    private void removeStale() {
        try {
            File dir = stagingDir();
            File[] files = dir.listFiles();
            if (files == null) return;
            String[] names = new String[files.length];
            long[] modified = new long[files.length];
            for (int i = 0; i < files.length; i++) {
                names[i] = files[i].getName();
                modified[i] = files[i].lastModified();
            }
            String keepId = prefs().getString(PREF_ID, null);
            String keep = SaveFileRules.validId(keepId) ? SaveFileRules.partName(keepId) : null;
            List<String> stale = SaveFileRules.stale(names, modified, System.currentTimeMillis(), keep);
            for (String name : stale) {
                //noinspection ResultOfMethodCallIgnored
                new File(dir, name).delete();
            }
        } catch (Exception e) {
            Log.w(TAG, "Could not tidy staged files", e);
        }
    }

    private File stagingDir() {
        File dir = new File(getContext().getCacheDir(), DIR);
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
        return dir;
    }

    private File partFile(String id) {
        return new File(stagingDir(), SaveFileRules.partName(id));
    }

    private SharedPreferences prefs() {
        return getContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** Kept on disk so a picker answer that outlives the app's process still saves the file. */
    private void remember(String id, String name) {
        SharedPreferences prefs = prefs();
        String previous = prefs.getString(PREF_ID, null);
        if (SaveFileRules.validId(previous) && !previous.equals(id)) {
            //noinspection ResultOfMethodCallIgnored
            partFile(previous).delete();
        }
        prefs.edit().putString(PREF_ID, id).putString(PREF_NAME, name).apply();
    }

    private void forget() {
        prefs().edit().remove(PREF_ID).remove(PREF_NAME).apply();
    }

    private void toast(Context context, String message) {
        main.post(() -> Toast.makeText(context, message, Toast.LENGTH_LONG).show());
    }

    private static String describe(String what, Exception e) {
        return what + ": " + e.getClass().getSimpleName() + (e.getMessage() != null ? ": " + e.getMessage() : "");
    }
}
