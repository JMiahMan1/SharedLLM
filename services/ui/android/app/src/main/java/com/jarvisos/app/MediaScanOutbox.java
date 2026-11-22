package com.jarvisos.app;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Photo checks the server could not run yet, and when to ask again.
 *
 * The photo check runs on the household's own vision model, on the GPU
 * computer. While that computer is off the server answers 503 with
 * {@code retry_after_s}: the picture was not checked, so it must not be marked
 * done, and the phone must not ask again at once. Two parts:
 *
 * <ul>
 * <li>{@link #retryAfterMs} and {@link #pausedUntil}: the phone holds every
 *     photo check until then (the gallery keeps its place, picture messages stay
 *     unseen), so an outage is a quiet wait, not a retry storm, and the child
 *     sees nothing.</li>
 * <li>a small on-disk queue for chat images taken from notifications, which
 *     exist only while the notification does: kept here (app-private storage, a
 *     downscaled thumbnail, at most {@link #MAX_PENDING}) and sent with the next
 *     gallery pass, instead of being lost.</li>
 * </ul>
 *
 * Plain Java on {@link File}s, so the rules are tested on the JVM.
 */
final class MediaScanOutbox {
    /** Kept thumbnails; past this the oldest go first. */
    static final int MAX_PENDING = 30;
    /** Without a usable answer from the server, wait this long. */
    static final long DEFAULT_RETRY_MS = 5L * 60 * 1000;
    static final long MIN_RETRY_MS = 30L * 1000;
    static final long MAX_RETRY_MS = 60L * 60 * 1000;

    private static final Pattern RETRY_AFTER = Pattern.compile("\"retry_after_s\"\\s*:\\s*(\\d+)");

    private MediaScanOutbox() {}

    /** How long to hold photo checks after a 503 with {@code body}, bounded. */
    static long retryAfterMs(String body) {
        if (body != null) {
            Matcher m = RETRY_AFTER.matcher(body);
            if (m.find()) {
                try {
                    long ms = Long.parseLong(m.group(1)) * 1000L;
                    return Math.max(MIN_RETRY_MS, Math.min(MAX_RETRY_MS, ms));
                } catch (NumberFormatException ignored) {
                    // fall through to the default
                }
            }
        }
        return DEFAULT_RETRY_MS;
    }

    /** The moment photo checks may resume, after a 503 answered at {@code nowMs}. */
    static long pausedUntil(long nowMs, String body) {
        return nowMs + retryAfterMs(body);
    }

    /** One kept check. */
    static final class Item {
        final File file;
        final String mediaType;
        final String fileName;
        final String b64;

        Item(File file, String mediaType, String fileName, String b64) {
            this.file = file;
            this.mediaType = mediaType;
            this.fileName = fileName;
            this.b64 = b64;
        }
    }

    /** Keep one thumbnail for later; the oldest is dropped past {@link #MAX_PENDING}. */
    static synchronized boolean add(File dir, String mediaType, String fileName, String b64) {
        if (b64 == null || b64.isEmpty()) return false;
        if (!dir.isDirectory() && !dir.mkdirs()) return false;
        String safeType = clean(mediaType);
        String safeName = clean(fileName);
        File f = new File(dir, System.currentTimeMillis() + "-" + Math.abs(b64.hashCode()) + ".scan");
        try (FileOutputStream out = new FileOutputStream(f)) {
            out.write((safeType + "\n" + safeName + "\n" + b64).getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            return false;
        }
        List<File> all = files(dir);
        for (int i = 0; i < all.size() - MAX_PENDING; i++) {
            //noinspection ResultOfMethodCallIgnored
            all.get(i).delete();
        }
        return true;
    }

    /** Kept checks, oldest first, at most {@code limit}. */
    static synchronized List<Item> pending(File dir, int limit) {
        List<Item> out = new ArrayList<>();
        for (File f : files(dir)) {
            if (out.size() >= limit) break;
            try {
                String text = new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8);
                String[] parts = text.split("\n", 3);
                if (parts.length == 3 && !parts[2].isEmpty()) {
                    out.add(new Item(f, parts[0], parts[1], parts[2]));
                } else {
                    //noinspection ResultOfMethodCallIgnored
                    f.delete(); // unreadable: nothing to send
                }
            } catch (Exception e) {
                //noinspection ResultOfMethodCallIgnored
                f.delete();
            }
        }
        return out;
    }

    /** The server took it (or will never take it): forget it. */
    static synchronized void done(Item item) {
        //noinspection ResultOfMethodCallIgnored
        item.file.delete();
    }

    static int size(File dir) {
        return files(dir).size();
    }

    private static List<File> files(File dir) {
        File[] found = dir.listFiles((d, name) -> name.endsWith(".scan"));
        if (found == null) return new ArrayList<>();
        Arrays.sort(found, (a, b) -> a.getName().compareTo(b.getName()));
        return new ArrayList<>(Arrays.asList(found));
    }

    private static String clean(String s) {
        return s == null ? "" : s.replace('\n', ' ').replace('\r', ' ');
    }
}
