package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.List;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

/**
 * The widget draws plain text, so these helpers are the only place the note
 * format is interpreted. They must keep the stored `# Title` / `Category:`
 * header out of the body and leave the markdown itself untouched on disk.
 */
public class NotesLinesTest {

    private static JSONObject note(String title, String path, String content) throws Exception {
        JSONObject note = new JSONObject();
        note.put("title", title);
        note.put("path", path);
        if (content != null) note.put("content", content);
        return note;
    }

    private static JSONObject detailWith(JSONObject... notes) throws Exception {
        JSONArray array = new JSONArray();
        for (JSONObject note : notes) array.put(note);
        JSONObject detail = new JSONObject();
        detail.put("notes", array);
        return detail;
    }

    @Test
    public void visiblePathKeepsArchivedAndTrashedNotesOut() {
        assertTrue(NotesLines.visiblePath("Notes/Trip ideas.md"));
        assertTrue(NotesLines.visiblePath("Notes/Recipes/Soup.md"));
        assertFalse(NotesLines.visiblePath("Notes/Archive/Trip ideas.md"));
        assertFalse(NotesLines.visiblePath("Notes/Trash/Trip ideas.md"));
        assertFalse(NotesLines.visiblePath(null));
    }

    @Test
    public void recentSkipsArchivedAndTrashedNotesAndHonoursTheLimit() throws Exception {
        JSONObject detail = detailWith(
                note("Trip ideas", "Notes/Trip ideas.md", "Buy milk"),
                note("Old draft", "Notes/Archive/Old draft.md", "Gone"),
                note("Deleted", "Notes/Trash/Deleted.md", "Gone"),
                note("Groceries", "Notes/Groceries.md", "Eggs"));

        List<NotesLines.Line> lines = NotesLines.recent(detail, 4);
        assertEquals(2, lines.size());
        assertEquals("Trip ideas", lines.get(0).title);
        assertEquals("Notes/Groceries.md", lines.get(1).path);

        assertEquals(1, NotesLines.recent(detail, 1).size());
        assertEquals(0, NotesLines.recent(null, 4).size());
    }

    @Test
    public void recentFallsBackToUntitledNote() throws Exception {
        List<NotesLines.Line> lines = NotesLines.recent(
                detailWith(note("", "Notes/Untitled.md", "Some text")), 4);
        assertEquals("Untitled note", lines.get(0).title);
    }

    @Test
    public void previewDropsTheStoredHeader() {
        assertEquals("Buy milk", NotesLines.preview("# Trip ideas\nCategory: Notes\n\nBuy milk"));
    }

    @Test
    public void previewRendersChecklistItems() {
        String preview = NotesLines.preview(
                "# Groceries\nCategory: Notes\n\n- [ ] milk\n- [x] eggs\n* [X] bread");
        assertEquals("\u2610 milk \u00b7 \u2611 eggs \u00b7 \u2611 bread", preview);
    }

    @Test
    public void previewCollapsesWhitespaceAndTruncatesLongText() {
        assertEquals("", NotesLines.preview(null));
        assertEquals("one \u00b7 two", NotesLines.preview("  one\t\ntwo  "));

        StringBuilder longText = new StringBuilder();
        for (int i = 0; i < 40; i++) longText.append("word ");
        String preview = NotesLines.preview(longText.toString());
        assertTrue(preview.length() <= NotesLines.MAX_BODY + 1);
        assertTrue(preview.endsWith("\u2026"));
    }

    @Test
    public void oneLineKeepsShortTextIntact() {
        assertEquals("Buy milk", NotesLines.oneLine(" Buy   milk ", 96));
    }

    @Test
    public void oneLineDropsTheEditorsStyleSpansButKeepsTheWords() {
        assertEquals("Buy red milk",
                NotesLines.oneLine("Buy <span style=\"color:#dc2626\">red</span> milk", 96));
    }
}
