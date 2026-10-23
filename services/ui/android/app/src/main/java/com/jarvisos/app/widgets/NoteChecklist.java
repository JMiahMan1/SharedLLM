package com.jarvisos.app.widgets;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Parsing for the single-note widget.
 *
 * The note on disk stays the same markdown the app and Nextcloud share. This
 * class only turns it into two things a widget can draw: checkbox rows the
 * person can tap ({@link Item}, whose text is exactly what
 * {@code /execute/note check_off} matches on) and plain body lines.
 */
final class NoteChecklist {
    static final int MAX_ITEM = 80;
    static final int MAX_LINE = 100;

    private static final Pattern CHECK_OPEN = Pattern.compile("^\\s*[-*]\\s*\\[\\s*]\\s*(.+)$");
    private static final Pattern CHECK_DONE = Pattern.compile("^\\s*[-*]\\s*\\[\\s*[xX]\\s*]\\s*(.+)$");
    private static final Pattern BULLET = Pattern.compile("^\\s*(?:[-*+]|\\d+[.)])\\s+(.+)$");
    private static final Pattern RULE = Pattern.compile("^\\s*(?:-{3,}|\\*{3,}|_{3,})\\s*$");

    static final class Item {
        final String text;
        final boolean done;

        Item(String text, boolean done) {
            this.text = text;
            this.done = done;
        }
    }

    private NoteChecklist() {}

    /** Checklist rows in file order, header stripped, limited to {@code limit}. */
    static List<Item> items(String content, int limit) {
        List<Item> out = new ArrayList<>();
        String body = NotesLines.stripHeader(content);
        if (body.isEmpty()) return out;
        for (String raw : body.split("\\r?\\n")) {
            if (out.size() >= limit) break;
            String line = raw.trim();
            if (line.isEmpty()) continue;
            Matcher done = CHECK_DONE.matcher(line);
            Matcher open = CHECK_OPEN.matcher(line);
            if (done.matches()) {
                out.add(new Item(NotesLines.oneLine(done.group(1), MAX_ITEM), true));
            } else if (open.matches()) {
                out.add(new Item(NotesLines.oneLine(open.group(1), MAX_ITEM), false));
            }
        }
        return out;
    }

    /** Body lines for a note without checkboxes: bullets cleaned, header out. */
    static List<String> lines(String content, int limit) {
        List<String> out = new ArrayList<>();
        String body = NotesLines.stripHeader(content);
        if (body.isEmpty()) return out;
        for (String raw : body.split("\\r?\\n")) {
            if (out.size() >= limit) break;
            String line = raw.trim();
            if (line.isEmpty() || RULE.matcher(line).matches()) continue;
            Matcher bullet = BULLET.matcher(line);
            if (bullet.matches()) line = "\u2022 " + bullet.group(1);
            out.add(NotesLines.oneLine(line, MAX_LINE));
        }
        return out;
    }
}
