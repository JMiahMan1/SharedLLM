package com.jarvisos.app;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.util.Arrays;
import java.util.Collections;

import org.junit.Test;

public class SaveFileRulesTest {

    private static final String A = "0123456789abcdef0123456789abcdef";
    private static final String B = "fedcba9876543210fedcba9876543210";

    @Test
    public void newIdsAreValidAndDistinct() {
        String one = SaveFileRules.newId();
        String two = SaveFileRules.newId();
        assertTrue(SaveFileRules.validId(one));
        assertTrue(SaveFileRules.validId(two));
        assertNotEquals(one, two);
    }

    @Test
    public void anIdFromTheWebSideIsNeverAPath() {
        assertTrue(SaveFileRules.validId(A));
        assertFalse(SaveFileRules.validId(null));
        assertFalse(SaveFileRules.validId(""));
        assertFalse(SaveFileRules.validId("../../shared_prefs/x"));
        assertFalse(SaveFileRules.validId(A.toUpperCase()));
        assertFalse(SaveFileRules.validId(A + "0"));
        assertFalse(SaveFileRules.validId(A.substring(1) + "/"));
    }

    @Test(expected = IllegalArgumentException.class)
    public void aPartNameRefusesABadId() {
        SaveFileRules.partName("../secret");
    }

    @Test
    public void aWaitingCallIsAnsweredOnEveryPath() {
        assertEquals(SaveFileRules.Outcome.WRITE, SaveFileRules.onResult(true, true, true, true));
        assertEquals(SaveFileRules.Outcome.CANCELLED, SaveFileRules.onResult(true, true, false, false));
        assertEquals(SaveFileRules.Outcome.CANCELLED, SaveFileRules.onResult(true, true, true, false));
        assertEquals(SaveFileRules.Outcome.CANCELLED, SaveFileRules.onResult(true, false, false, true));
        assertEquals(SaveFileRules.Outcome.STAGED_FILE_MISSING, SaveFileRules.onResult(true, false, true, true));
    }

    @Test
    public void anAnswerThatOutlivedTheProcessStillSavesTheStagedFile() {
        assertEquals(SaveFileRules.Outcome.RECOVER, SaveFileRules.onResult(false, true, true, true));
        assertEquals(SaveFileRules.Outcome.DISCARD_ORPHAN, SaveFileRules.onResult(false, true, false, false));
        assertEquals(SaveFileRules.Outcome.NOTHING, SaveFileRules.onResult(false, false, true, true));
        assertEquals(SaveFileRules.Outcome.NOTHING, SaveFileRules.onResult(false, false, false, false));
    }

    @Test
    public void anAnswerIsLostOnlyAfterThePickerTookTheScreen() {
        assertTrue(SaveFileRules.answerLost(true, true, true));
        // The picker is still opening, or the app never left the screen for it.
        assertFalse(SaveFileRules.answerLost(true, false, false));
        assertFalse(SaveFileRules.answerLost(true, true, false));
        assertFalse(SaveFileRules.answerLost(false, true, true));
    }

    @Test
    public void staleStagedFilesGoButTheOneAPickerMayAnswerStays() {
        long now = 10 * SaveFileRules.STALE_AFTER_MS;
        long old = now - SaveFileRules.STALE_AFTER_MS;
        long fresh = now - 1000;
        String[] names = {A + ".part", B + ".part", "notes.txt", "x.part", A + ".tmp"};
        long[] modified = {old, old, old, old, old};
        assertEquals(Collections.singletonList(B + ".part"), SaveFileRules.stale(names, modified, now, A + ".part"));
        assertEquals(Arrays.asList(A + ".part", B + ".part"), SaveFileRules.stale(names, modified, now, null));
        long[] recent = {fresh, fresh, old, old, old};
        assertTrue(SaveFileRules.stale(names, recent, now, null).isEmpty());
        assertTrue(SaveFileRules.stale(null, null, now, null).isEmpty());
    }

    @Test
    public void copyMovesEveryByteOfALargeFile() throws Exception {
        byte[] data = new byte[3 * 1024 * 1024 + 17];
        for (int i = 0; i < data.length; i++) data[i] = (byte) (i * 31);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        long n = SaveFileRules.copy(new ByteArrayInputStream(data), out);
        assertEquals(data.length, n);
        assertArrayEquals(data, out.toByteArray());
    }

    @Test
    public void copyOfAnEmptyFileWritesNothing() throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        assertEquals(0, SaveFileRules.copy(new ByteArrayInputStream(new byte[0]), out));
        assertEquals(0, out.size());
    }
}
