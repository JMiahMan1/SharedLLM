package com.jarvisos.app;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * The decisions behind SaveFilePlugin, kept free of Android so they run on
 * the desktop JVM.
 *
 * The bytes of a file being saved never sit in a PluginCall: the web side
 * streams them into a staged file in the app's cache first ({@link #partName}),
 * and the "Save to" picker's answer copies that file to the place chosen.
 * A PluginCall holding the whole file used to be written into the activity's
 * saved state when the picker covered the app, which crashed the app with
 * TransactionTooLargeException for anything over about half a megabyte.
 */
final class SaveFileRules {

    private SaveFileRules() {}

    /** Staged files left by a save that never finished are removed after this long. */
    static final long STALE_AFTER_MS = 24L * 60 * 60 * 1000;

    static final String PART_SUFFIX = ".part";

    private static final Pattern ID = Pattern.compile("[0-9a-f]{32}");

    /** A new id for a staged file: 32 hex characters, safe as a file name. */
    static String newId() {
        return UUID.randomUUID().toString().replace("-", "").toLowerCase(Locale.ROOT);
    }

    /** Whether an id from the web side is one this class could have made (never a path). */
    static boolean validId(String id) {
        return id != null && ID.matcher(id).matches();
    }

    /** The staged file's name in the cache for an id. */
    static String partName(String id) {
        if (!validId(id)) throw new IllegalArgumentException("Not a staged file id");
        return id + PART_SUFFIX;
    }

    /** What to do with the picker's answer. */
    enum Outcome {
        /** Copy the staged file to the chosen place and answer the waiting call. */
        WRITE,
        /** The person backed out: drop the staged file, answer "not saved". */
        CANCELLED,
        /** A place was chosen but the staged file is gone: the waiting call fails. */
        STAGED_FILE_MISSING,
        /**
         * No call is waiting (Android ended the app while the picker was up)
         * but the staged file survived: still save it where the person chose.
         */
        RECOVER,
        /** No call is waiting and the person backed out: drop the staged file. */
        DISCARD_ORPHAN,
        /** Nothing to save. */
        NOTHING,
    }

    static Outcome onResult(boolean callWaiting, boolean stagedFileExists, boolean resultOk, boolean hasUri) {
        boolean chosen = resultOk && hasUri;
        if (callWaiting) {
            if (!chosen) return Outcome.CANCELLED;
            return stagedFileExists ? Outcome.WRITE : Outcome.STAGED_FILE_MISSING;
        }
        if (!stagedFileExists) return Outcome.NOTHING;
        return chosen ? Outcome.RECOVER : Outcome.DISCARD_ORPHAN;
    }

    /**
     * Whether a waiting save has lost its answer: the picker was opened, the
     * app went to the background for it, and the app is in front again with
     * still no answer. Android always delivers the answer before the app is
     * back in front, so the call would otherwise wait forever.
     */
    static boolean answerLost(boolean callWaiting, boolean pickerOpened, boolean pausedSincePickerOpened) {
        return callWaiting && pickerOpened && pausedSincePickerOpened;
    }

    /**
     * The staged files to delete: any older than {@link #STALE_AFTER_MS},
     * except the one a picker may still answer for. Names that are not staged
     * files are never touched.
     */
    static List<String> stale(String[] names, long[] modifiedMs, long nowMs, String keepName) {
        List<String> out = new ArrayList<>();
        if (names == null || modifiedMs == null) return out;
        for (int i = 0; i < names.length && i < modifiedMs.length; i++) {
            String name = names[i];
            if (name == null || !name.endsWith(PART_SUFFIX)) continue;
            if (!validId(name.substring(0, name.length() - PART_SUFFIX.length()))) continue;
            if (name.equals(keepName)) continue;
            if (nowMs - modifiedMs[i] >= STALE_AFTER_MS) out.add(name);
        }
        return out;
    }

    /** Copies everything, returning the byte count. Closes neither stream. */
    static long copy(InputStream in, OutputStream out) throws IOException {
        byte[] buffer = new byte[64 * 1024];
        long total = 0;
        int n;
        while ((n = in.read(buffer)) != -1) {
            out.write(buffer, 0, n);
            total += n;
        }
        out.flush();
        return total;
    }
}
