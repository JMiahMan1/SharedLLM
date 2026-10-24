package com.jarvisos.app;

import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Just enough DNS for the web filter: read a query's name and type, answer
 * "no such site", ask for a different name, and answer with A records taken
 * from another answer. Plain Java, JVM-tested; anything malformed throws, and
 * the filter then forwards the query untouched.
 */
final class DnsMessage {

    static final int TYPE_A = 1;
    static final int RCODE_NXDOMAIN = 3;

    static final class Record {
        final long ttl;
        final byte[] address;

        Record(long ttl, byte[] address) {
            this.ttl = ttl;
            this.address = address;
        }
    }

    private DnsMessage() {}

    /** The first question's name, lower case, no trailing dot. */
    static String questionName(byte[] msg) {
        StringBuilder out = new StringBuilder();
        int at = 12;
        while (true) {
            int len = msg[at] & 0xff;
            if (len == 0) break;
            if ((len & 0xc0) != 0 || len > 63) throw new IllegalArgumentException("label");
            if (out.length() > 0) out.append('.');
            out.append(new String(msg, at + 1, len, java.nio.charset.StandardCharsets.US_ASCII));
            at += 1 + len;
        }
        return out.toString().toLowerCase(Locale.ROOT);
    }

    /** Offset just past the first question (name, type, class). */
    static int questionEnd(byte[] msg) {
        if (msg.length < 12 || u16(msg, 4) < 1) throw new IllegalArgumentException("no question");
        int at = skipName(msg, 12);
        if (at + 4 > msg.length) throw new IllegalArgumentException("short");
        return at + 4;
    }

    static int questionType(byte[] msg) {
        return u16(msg, questionEnd(msg) - 4);
    }

    /** An answer to ``query`` with no records and the given response code. */
    static byte[] reply(byte[] query, int rcode) {
        int end = questionEnd(query);
        byte[] out = new byte[end];
        System.arraycopy(query, 0, out, 0, end);
        out[2] = (byte) (0x80 | (query[2] & 0x79)); // QR, opcode and RD from the query
        out[3] = (byte) (0x80 | (rcode & 0x0f));    // RA
        put16(out, 4, 1);
        put16(out, 6, 0);
        put16(out, 8, 0);
        put16(out, 10, 0);
        return out;
    }

    /** A recursive query for ``name`` of ``type``. */
    static byte[] query(int id, String name, int type) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(id >> 8);
        out.write(id);
        out.write(0x01); // RD
        out.write(0x00);
        out.write(0);
        out.write(1);
        for (int i = 0; i < 6; i++) out.write(0);
        for (String label : name.split("\\.")) {
            byte[] b = label.getBytes(java.nio.charset.StandardCharsets.US_ASCII);
            if (b.length == 0 || b.length > 63) throw new IllegalArgumentException("label");
            out.write(b.length);
            out.write(b, 0, b.length);
        }
        out.write(0);
        out.write(type >> 8);
        out.write(type);
        out.write(0);
        out.write(1); // IN
        return out.toByteArray();
    }

    /** The A records in an answer. */
    static List<Record> aRecords(byte[] msg) {
        List<Record> out = new ArrayList<>();
        int questions = u16(msg, 4);
        int answers = u16(msg, 6);
        int at = 12;
        for (int i = 0; i < questions; i++) at = skipName(msg, at) + 4;
        for (int i = 0; i < answers; i++) {
            at = skipName(msg, at);
            int type = u16(msg, at);
            long ttl = ((long) u16(msg, at + 4) << 16) | u16(msg, at + 6);
            int rdlen = u16(msg, at + 8);
            at += 10;
            if (at + rdlen > msg.length) throw new IllegalArgumentException("short");
            if (type == TYPE_A && rdlen == 4) {
                byte[] ip = new byte[4];
                System.arraycopy(msg, at, ip, 0, 4);
                out.add(new Record(ttl, ip));
            }
            at += rdlen;
        }
        return out;
    }

    /** ``query`` answered with ``records`` under its own name. */
    static byte[] answerWith(byte[] query, List<Record> records) {
        byte[] head = reply(query, 0);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(head, 0, head.length);
        for (Record r : records) {
            out.write(0xc0); // pointer to the question's name
            out.write(12);
            out.write(0);
            out.write(TYPE_A);
            out.write(0);
            out.write(1);
            long ttl = Math.min(r.ttl, 300);
            out.write((int) (ttl >> 24));
            out.write((int) (ttl >> 16));
            out.write((int) (ttl >> 8));
            out.write((int) ttl);
            out.write(0);
            out.write(4);
            out.write(r.address, 0, 4);
        }
        byte[] msg = out.toByteArray();
        put16(msg, 6, records.size());
        return msg;
    }

    static int id(byte[] msg) {
        return u16(msg, 0);
    }

    private static int skipName(byte[] msg, int at) {
        while (true) {
            if (at >= msg.length) throw new IllegalArgumentException("name");
            int len = msg[at] & 0xff;
            if (len == 0) return at + 1;
            if ((len & 0xc0) == 0xc0) return at + 2;
            at += 1 + len;
        }
    }

    static int u16(byte[] b, int at) {
        return ((b[at] & 0xff) << 8) | (b[at + 1] & 0xff);
    }

    static void put16(byte[] b, int at, int v) {
        b[at] = (byte) (v >> 8);
        b[at + 1] = (byte) v;
    }
}
