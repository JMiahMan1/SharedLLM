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

    @Test
    public void keepsTheMessageContextWithTheKeptCheck() throws Exception {
        File dir = Files.createTempDirectory("scan-outbox-meta").toFile();
        String meta = "{\"source\":\"notification\",\"contact\":\"Alex\",\"message_text\":\"look at this\"}";
        assertTrue(MediaScanOutbox.add(dir, "image", "chat.jpg", "AAAA", meta));

        MediaScanOutbox.Item item = MediaScanOutbox.pending(dir, 1).get(0);
        assertEquals("chat.jpg", item.fileName);
        assertEquals("the context must not become part of the image", "AAAA", item.b64);
        assertEquals(meta, item.metadata);
    }

    @Test
    public void readsACheckQueuedBeforeContextWasKept() throws Exception {
        // What an older version wrote: three lines, no context. A phone that
        // upgrades must send it rather than treat it as unreadable and delete it.
        File dir = Files.createTempDirectory("scan-outbox-legacy").toFile();
        File old = new File(dir, "1700000000000-1.scan");
        Files.write(old.toPath(), "image\nold.jpg\nBBBB".getBytes(java.nio.charset.StandardCharsets.UTF_8));

        List<MediaScanOutbox.Item> pending = MediaScanOutbox.pending(dir, 4);
        assertEquals(1, pending.size());
        assertEquals("old.jpg", pending.get(0).fileName);
        assertEquals("BBBB", pending.get(0).b64);
        assertEquals(null, pending.get(0).metadata);
    }
}
