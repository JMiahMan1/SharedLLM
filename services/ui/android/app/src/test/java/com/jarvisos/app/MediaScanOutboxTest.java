package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.nio.file.Files;
import java.util.List;

import org.junit.Test;

public class MediaScanOutboxTest {

    @Test
    public void holdsPhotoChecksForTheTimeTheServerAsks() {
        assertEquals(300_000L, MediaScanOutbox.retryAfterMs("{\"detail\":\"x\",\"retry_after_s\":300}"));
        assertEquals(1_300_000L, MediaScanOutbox.pausedUntil(1_000_000L, "{\"retry_after_s\": 300}"));
    }

    @Test
    public void boundsTheWaitAndHasADefault() {
        assertEquals(MediaScanOutbox.MIN_RETRY_MS, MediaScanOutbox.retryAfterMs("{\"retry_after_s\":1}"));
        assertEquals(MediaScanOutbox.MAX_RETRY_MS, MediaScanOutbox.retryAfterMs("{\"retry_after_s\":999999}"));
        assertEquals(MediaScanOutbox.DEFAULT_RETRY_MS, MediaScanOutbox.retryAfterMs("<html>Bad gateway</html>"));
        assertEquals(MediaScanOutbox.DEFAULT_RETRY_MS, MediaScanOutbox.retryAfterMs(null));
    }

    @Test
    public void keepsNotificationImagesUntilTheyAreSentOldestFirst() throws Exception {
        File dir = Files.createTempDirectory("scan-outbox").toFile();
        assertTrue(MediaScanOutbox.add(dir, "image", "first.jpg", "AAAA"));
        Thread.sleep(2);
        assertTrue(MediaScanOutbox.add(dir, "image", "second.jpg", "BBBB"));
        List<MediaScanOutbox.Item> pending = MediaScanOutbox.pending(dir, 8);
        assertEquals(2, pending.size());
        assertEquals("first.jpg", pending.get(0).fileName);
        assertEquals("AAAA", pending.get(0).b64);
        MediaScanOutbox.done(pending.get(0));
        assertEquals(1, MediaScanOutbox.size(dir));
        assertEquals("second.jpg", MediaScanOutbox.pending(dir, 8).get(0).fileName);
    }

    @Test
    public void dropsTheOldestPastTheCap() throws Exception {
        File dir = Files.createTempDirectory("scan-outbox-cap").toFile();
        for (int i = 0; i < MediaScanOutbox.MAX_PENDING + 5; i++) {
            MediaScanOutbox.add(dir, "image", "n" + i + ".jpg", "B64-" + i);
            Thread.sleep(1);
        }
        assertEquals(MediaScanOutbox.MAX_PENDING, MediaScanOutbox.size(dir));
        assertEquals("n5.jpg", MediaScanOutbox.pending(dir, 1).get(0).fileName);
    }
}
