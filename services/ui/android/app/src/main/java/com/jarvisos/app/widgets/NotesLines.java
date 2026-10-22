package com.jarvisos.app.widgets;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Pure parsing for the Notes home-screen widget.
 *
 * The gateway returns the same note files the app edits: a legacy
 * {@code # Title} / {@code Category:} header followed by markdown. A widget
 * row wants a single readable line, so the header is dropped and checklist
 * markers become boxes instead of raw markdown.
 */
final class NotesLines {
    static final int MAX_TITLE = 60;
    static final int MAX_BODY = 96;

    private static final Pattern CHECK_OPEN = Pattern.compile("^\\s*[-*]\\s*\\[\\s*]\\s*(.*)$");
    private static final Pattern CHECK_DONE = Pattern.compile("^\\s*[-*]\\s*\\[\\s*[xX]\\s*]\\s*(.*)$");
    private static final Pattern HEADER_TITLE = Pattern.compile("^#\\s+.*$");
    private static final Pattern HEADER_CATEGORY = Pattern.compile("^Category:\\s*.*$");
    private static final Pattern WHITESPACE = Pattern.compile("\\s+");

    static final class Line {
        final String path;
        final String title;
        final String body;

        Line(String path, String title, String body) {
            this.path = path;
            this.title = title;
            this.body = body;
        }
    }

    private NotesLines() {}

    /** Skip the folders the app uses for archive and trash. */
    static boolean visiblePath(String path) {
        if (path == null) return false;
        return !path.startsWith("Notes/Archive/") && !path.startsWith("Notes/Trash/");
    }

    /**
     * Newest notes from a {@code /api/communication/notes/list} reply's
     * {@code detail} object, ready for the widget rows.
     */
    static List<Line> recent(JSONObject detail, int limit) {
        List<Line> lines = new ArrayList<>();
        if (detail == null) return lines;
        JSONArray notes = detail.optJSONArray("notes");
        if (notes == null) return lines;
        for (int i = 0; i < notes.length() && lines.size() < limit; i++) {
            JSONObject note = notes.optJSONObject(i);
            if (note == null) continue;
            String path = note.optString("path", "");
            if (!visiblePath(path)) continue;
            String title = oneLine(note.optString("title", ""), MAX_TITLE);
            if (title.isEmpty()) title = "Untitled note";
            lines.add(new Line(path, title, preview(note.optString("content", ""))));
        }
        return lines;
    }

    /** One readable line from the note body: no header, no raw checklist. */
    static String preview(String content) {
        if (content == null || content.isEmpty()) return "";
        StringBuilder body = new StringBuilder();
        boolean inHeader = true;
        for (String raw : content.split("\\r?\\n")) {
            String line = raw.trim();
            if (inHeader && line.isEmpty()) continue;
            if (inHeader && (HEADER_TITLE.matcher(line).matches() || HEADER_CATEGORY.matcher(line).matches())) {
                continue;
            }
            inHeader = false;
            if (line.isEmpty()) continue;
            Matcher done = CHECK_DONE.matcher(line);
            Matcher open = CHECK_OPEN.matcher(line);
            if (done.matches()) {
                line = "\u2611 " + done.group(1);
            } else if (open.matches()) {
                line = "\u2610 " + open.group(1);
            }
            if (body.length() > 0) body.append(" \u00b7 ");
            body.append(line);
            if (body.length() >= MAX_BODY * 2) break;
        }
        return oneLine(body.toString(), MAX_BODY);
    }

    /** Collapse whitespace and truncate on a word boundary. */
    static String oneLine(String text, int max) {
        if (text == null) return "";
        String collapsed = WHITESPACE.matcher(text).replaceAll(" ").trim();
        if (collapsed.length() <= max) return collapsed;
        String cut = collapsed.substring(0, max);
        int space = cut.lastIndexOf(' ');
        if (space > max / 2) cut = cut.substring(0, space);
        return cut.trim() + "\u2026";
    }
}
