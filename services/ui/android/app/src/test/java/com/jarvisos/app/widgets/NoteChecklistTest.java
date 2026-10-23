package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.List;

import org.junit.Test;

/**
 * The single-note widget draws from this parser only. Checklist text has to
 * come back exactly as stored, because check_off matches the item text in the
 * file; the header stays out of both views.
 */
public class NoteChecklistTest {

    private static final String NOTE =
        "# Groceries\n"
        + "Category: Home\n"
        + "\n"
        + "- [ ] Milk\n"
        + "- [x] Eggs\n"
        + "Some plain line\n"
        + "- [ ] Bread\n";

    @Test
    public void itemsKeepChecklistTextAndState() {
        List<NoteChecklist.Item> items = NoteChecklist.items(NOTE, 5);
        assertEquals(3, items.size());
        assertEquals("Milk", items.get(0).text);
        assertFalse(items.get(0).done);
        assertEquals("Eggs", items.get(1).text);
        assertTrue(items.get(1).done);
        assertEquals("Bread", items.get(2).text);
    }

    @Test
    public void itemsRespectTheRowLimit() {
        List<NoteChecklist.Item> items = NoteChecklist.items(NOTE, 2);
        assertEquals(2, items.size());
        assertEquals("Milk", items.get(0).text);
        assertEquals("Eggs", items.get(1).text);
    }

    @Test
    public void linesDropTheHeaderAndCleanBullets() {
        String plain =
            "# Trip\n"
            + "Category: Travel\n"
            + "\n"
            + "First stop\n"
            + "---\n"
            + "- Pack bags\n"
            + "3. Book hotel\n";
        List<String> lines = NoteChecklist.lines(plain, 5);
        assertEquals(3, lines.size());
        assertEquals("First stop", lines.get(0));
        assertEquals("\u2022 Pack bags", lines.get(1));
        assertEquals("\u2022 Book hotel", lines.get(2));
    }

    @Test
    public void aPlainNoteHasNoChecklistItems() {
        assertTrue(NoteChecklist.items("Just a thought", 3).isEmpty());
        assertFalse(NoteChecklist.lines("Just a thought", 3).isEmpty());
    }
}
