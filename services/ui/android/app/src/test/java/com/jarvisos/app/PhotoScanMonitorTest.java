package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.HashSet;
import java.util.Set;

import org.junit.Test;

public class PhotoScanMonitorTest {

    @Test
    public void allowsSupportedImagesAndVideosOnProtectedPhoneWithScanEnabled() {
        Set<String> seen = new HashSet<>();
        assertTrue(PhotoScanMonitor.shouldScan(true, true, "image/jpeg", "vacation.jpg", 2048L, seen, "1"));
        assertTrue(PhotoScanMonitor.shouldScan(true, true, "image/png", "screenshot.png", 50000L, seen, "2"));
        assertTrue(PhotoScanMonitor.shouldScan(true, true, "video/mp4", "clip.mp4", 100000L, seen, "3"));
        assertTrue(PhotoScanMonitor.shouldScan(true, true, null, "photo.heic", 4096L, seen, "4"));

        assertEquals("image", PhotoScanMonitor.mediaType("image/jpeg", "vacation.jpg"));
        assertEquals("video", PhotoScanMonitor.mediaType("video/mp4", "clip.mp4"));
        assertEquals("video", PhotoScanMonitor.mediaType(null, "clip.mp4"));
    }

    @Test
    public void rejectsWhenDisabledAdultPhoneTooSmallOrAlreadySeen() {
        Set<String> seen = new HashSet<>();
        seen.add("seen_1");

        // Disabled or adult phone
        assertFalse(PhotoScanMonitor.shouldScan(false, true, "image/jpeg", "pic.jpg", 4096L, seen, "new"));
        assertFalse(PhotoScanMonitor.shouldScan(true, false, "image/jpeg", "pic.jpg", 4096L, seen, "new"));

        // File too small (<1KB) or too large (>50MB)
        assertFalse(PhotoScanMonitor.shouldScan(true, true, "image/jpeg", "tiny.jpg", 500L, seen, "new"));
        assertFalse(PhotoScanMonitor.shouldScan(true, true, "image/jpeg", "huge.jpg", 60 * 1024 * 1024L, seen, "new"));

        // Already seen
        assertFalse(PhotoScanMonitor.shouldScan(true, true, "image/jpeg", "old.jpg", 4096L, seen, "seen_1"));

        // Unsupported type
        assertFalse(PhotoScanMonitor.shouldScan(true, true, "application/pdf", "doc.pdf", 4096L, seen, "new"));
    }
}
