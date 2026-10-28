package com.jarvisos.app;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * On-device media evaluation for safety scanning (Bark / Qustodio style).
 * Decides whether an image or video file added to the device gallery or
 * downloads should be sampled and scanned for content concerns.
 *
 * Plain Java with no Android types, so every case is unit-tested on the JVM.
 */
public final class PhotoScanMonitor {

    static final long MIN_IMAGE_SIZE_BYTES = 1024L;             // Skip tiny 1KB icons
    static final long MAX_IMAGE_SIZE_BYTES = 50 * 1024 * 1024L; // 50MB max file size

    static final Set<String> SUPPORTED_IMAGE_MIMES = new HashSet<>(Arrays.asList(
            "image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"));

    static final Set<String> SUPPORTED_VIDEO_MIMES = new HashSet<>(Arrays.asList(
            "video/mp4", "video/3gpp", "video/webm", "video/quicktime"));

    static final Set<String> SUPPORTED_EXTENSIONS = new HashSet<>(Arrays.asList(
            "jpg", "jpeg", "png", "webp", "heic", "heif", "mp4", "mov"));

    private PhotoScanMonitor() {}

    /**
     * Whether a media item on a protected phone should be submitted for safety scanning.
     */
    public static boolean shouldScan(boolean protectedPhone, boolean photoScanEnabled,
                                     String mimeType, String fileName, long sizeBytes,
                                     Set<String> seenIds, String mediaId) {
        if (!protectedPhone || !photoScanEnabled) return false;
        if (sizeBytes < MIN_IMAGE_SIZE_BYTES || sizeBytes > MAX_IMAGE_SIZE_BYTES) return false;
        if (mediaId != null && seenIds != null && seenIds.contains(mediaId)) return false;

        String mime = mimeType == null ? "" : mimeType.trim().toLowerCase(Locale.US);
        if (SUPPORTED_IMAGE_MIMES.contains(mime) || SUPPORTED_VIDEO_MIMES.contains(mime)) {
            return true;
        }

        if (fileName != null && fileName.contains(".")) {
            String ext = fileName.substring(fileName.lastIndexOf('.') + 1).trim().toLowerCase(Locale.US);
            return SUPPORTED_EXTENSIONS.contains(ext);
        }

        return false;
    }

    /** Resolve media type category ("image" or "video") from mime or file name. */
    public static String mediaType(String mimeType, String fileName) {
        String mime = mimeType == null ? "" : mimeType.trim().toLowerCase(Locale.US);
        if (mime.startsWith("video/") || (fileName != null && (fileName.endsWith(".mp4") || fileName.endsWith(".mov")))) {
            return "video";
        }
        return "image";
    }
}
