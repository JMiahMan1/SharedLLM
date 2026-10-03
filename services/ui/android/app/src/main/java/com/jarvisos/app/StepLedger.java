package com.jarvisos.app;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.util.ArrayList;
import java.util.List;

/**
 * Durable, reboot-safe step ledger — the on-device source of truth.
 *
 * The hardware TYPE_STEP_COUNTER only reports steps since boot, so any "today"
 * number is app arithmetic and a reboot destroys the raw baseline. This ledger
 * persists a per-day total (a rollup, not every sample) so history survives
 * reboots, app updates and days when the app never opened. Day totals are the
 * contract: whatever computes them, the ledger is what gets synced and
 * backfilled, and it is the only thing allowed to speak for past days.
 *
 * The database is intentionally tiny: one row per day plus a small key/value
 * table for the counter anchor.
 */
public class StepLedger extends SQLiteOpenHelper {

    private static final String DB_NAME = "step_ledger.db";
    /**
     * 2 adds the `hours` table. The bump is deliberately additive: the previous
     * {@link #onUpgrade} dropped every table, so simply raising the version
     * would have thrown away the day history this class exists to protect.
     */
    private static final int DB_VERSION = 2;
    public static final String SOURCE_PHONE = "phone";

    public StepLedger(Context context) {
        super(context, DB_NAME, null, DB_VERSION);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL(
            "CREATE TABLE days (" +
            "  day TEXT NOT NULL," +
            "  source TEXT NOT NULL DEFAULT 'phone'," +
            "  steps INTEGER NOT NULL DEFAULT 0," +
            "  updated_at INTEGER NOT NULL DEFAULT 0," +
            "  PRIMARY KEY (day, source)" +
            ")"
        );
        db.execSQL(
            "CREATE TABLE hours (" +
            "  day TEXT NOT NULL," +
            "  hour INTEGER NOT NULL," +
            "  source TEXT NOT NULL DEFAULT 'phone'," +
            "  steps INTEGER NOT NULL DEFAULT 0," +
            "  updated_at INTEGER NOT NULL DEFAULT 0," +
            "  PRIMARY KEY (day, hour, source)" +
            ")"
        );
        db.execSQL("CREATE INDEX hours_day ON hours (day)");
        db.execSQL(
            "CREATE TABLE anchor (" +
            "  k TEXT PRIMARY KEY," +
            "  v TEXT NOT NULL" +
            ")"
        );
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        if (oldVersion < 2) {
            // Version 1 installs already hold real day history; adding a table
            // is the whole migration. Anything older than that predates the
            // ledger contract, and there is nothing worth migrating.
            db.execSQL(
                "CREATE TABLE IF NOT EXISTS hours (" +
                "  day TEXT NOT NULL," +
                "  hour INTEGER NOT NULL," +
                "  source TEXT NOT NULL DEFAULT 'phone'," +
                "  steps INTEGER NOT NULL DEFAULT 0," +
                "  updated_at INTEGER NOT NULL DEFAULT 0," +
                "  PRIMARY KEY (day, hour, source)" +
                ")"
            );
            db.execSQL("CREATE INDEX IF NOT EXISTS hours_day ON hours (day)");
            return;
        }
        // The ledger is a cache of history the server also has; rebuilding is
        // acceptable and safer than a half-migrated table.
        db.execSQL("DROP TABLE IF EXISTS days");
        db.execSQL("DROP TABLE IF EXISTS hours");
        db.execSQL("DROP TABLE IF EXISTS anchor");
        onCreate(db);
    }

    /**
     * Upsert a day's total, keeping the max: a reboot restarts the raw counter
     * and a later, smaller reading must never reduce a recorded day.
     */
    public void recordDay(String day, int steps, String source) {
        if (day == null || day.isEmpty() || steps < 0) return;
        if (source == null || source.isEmpty()) source = SOURCE_PHONE;
        SQLiteDatabase db = getWritableDatabase();
        try {
            Cursor cursor = db.query(
                "days", new String[]{"steps"}, "day = ? AND source = ?",
                new String[]{day, source}, null, null, null
            );
            int existing = 0;
            try {
                if (cursor.moveToFirst()) {
                    existing = cursor.getInt(0);
                }
            } finally {
                cursor.close();
            }
            if (steps < existing) return;
            ContentValues values = new ContentValues();
            values.put("day", day);
            values.put("source", source);
            values.put("steps", steps);
            values.put("updated_at", System.currentTimeMillis());
            db.insertWithOnConflict("days", null, values, SQLiteDatabase.CONFLICT_REPLACE);
        } finally {
            db.close();
        }
    }

    /**
     * One-time repair hook: write a corrected total even when it is lower than
     * the previously recorded (buggy) value. Normal writes must keep using
     * {@link #recordDay}'s max semantics; this exists only for the migration
     * that subtracts a midnight-carry error out of today's row.
     */
    public void replaceDay(String day, int steps, String source) {
        if (day == null || day.isEmpty() || steps < 0) return;
        if (source == null || source.isEmpty()) source = SOURCE_PHONE;
        SQLiteDatabase db = getWritableDatabase();
        try {
            ContentValues values = new ContentValues();
            values.put("day", day);
            values.put("source", source);
            values.put("steps", steps);
            values.put("updated_at", System.currentTimeMillis());
            db.insertWithOnConflict("days", null, values, SQLiteDatabase.CONFLICT_REPLACE);
        } finally {
            db.close();
        }
    }

    /** Read one day's total for a source (0 when unrecorded). */
    public int daySteps(String day, String source) {
        SQLiteDatabase db = getReadableDatabase();
        try {
            Cursor cursor = db.query(
                "days", new String[]{"steps"}, "day = ? AND source = ?",
                new String[]{day, source == null ? SOURCE_PHONE : source}, null, null, null
            );
            try {
                return cursor.moveToFirst() ? cursor.getInt(0) : 0;
            } finally {
                cursor.close();
            }
        } finally {
            db.close();
        }
    }

    /** One row of the ledger, oldest first when requested through history(). */
    public static class DayEntry {
        public final String day;
        public final String source;
        public final int steps;
        public final long updatedAt;

        DayEntry(String day, String source, int steps, long updatedAt) {
            this.day = day;
            this.source = source;
            this.steps = steps;
            this.updatedAt = updatedAt;
        }
    }

    /**
     * Most recent ledger days for a source, newest first, limited to `days`.
     * This is what the client backfills to the server after a gap.
     */
    public List<DayEntry> history(int days, String source) {
        int limit = Math.max(1, Math.min(days, 400));
        List<DayEntry> out = new ArrayList<>();
        SQLiteDatabase db = getReadableDatabase();
        try {
            Cursor cursor = db.query(
                "days", new String[]{"day", "source", "steps", "updated_at"},
                "source = ?", new String[]{source == null ? SOURCE_PHONE : source},
                null, null, "day DESC", String.valueOf(limit)
            );
            try {
                while (cursor.moveToNext()) {
                    out.add(new DayEntry(
                        cursor.getString(0), cursor.getString(1),
                        cursor.getInt(2), cursor.getLong(3)
                    ));
                }
            } finally {
                cursor.close();
            }
        } finally {
            db.close();
        }
        return out;
    }

    /** Days newer than `sinceDay` (exclusive), oldest first, for backfill. */
    public List<DayEntry> daysSince(String sinceDay, int max) {
        List<DayEntry> all = history(max, SOURCE_PHONE);
        List<DayEntry> out = new ArrayList<>();
        for (int i = all.size() - 1; i >= 0; i--) { // newest-first -> oldest-first
            DayEntry entry = all.get(i);
            if (sinceDay != null && entry.day.compareTo(sinceDay) <= 0) continue;
            out.add(entry);
        }
        return out;
    }

    /** One hour of steps for one source. */
    public static class HourEntry {
        public final String day;
        public final int hour;
        public final String source;
        public final int steps;
        public final long updatedAt;

        HourEntry(String day, int hour, String source, int steps, long updatedAt) {
            this.day = day;
            this.hour = hour;
            this.source = source;
            this.steps = steps;
            this.updatedAt = updatedAt;
        }
    }

    /**
     * Add `steps` to an hour bucket.
     *
     * Additive rather than max-keeping: an hour is credited with the deltas
     * observed while it was the current hour, so successive readings must
     * accumulate. A reboot leaves recorded hours untouched (the counter restart
     * is not a walking event), which is why this is a plain addition and not a
     * max: there is nothing to keep the maximum of.
     */
    public void addHour(String day, int hour, int steps, String source) {
        if (day == null || day.isEmpty() || hour < 0 || hour > 23 || steps <= 0) return;
        if (source == null || source.isEmpty()) source = SOURCE_PHONE;
        SQLiteDatabase db = getWritableDatabase();
        try {
            db.execSQL(
                "INSERT INTO hours (day, hour, source, steps, updated_at) VALUES (?, ?, ?, ?, ?) " +
                "ON CONFLICT(day, hour, source) DO UPDATE SET " +
                "  steps = steps + excluded.steps, updated_at = excluded.updated_at",
                new Object[]{day, hour, source, steps, System.currentTimeMillis()}
            );
        } finally {
            db.close();
        }
    }

    /**
     * Hour buckets for one local day, oldest hour first.
     *
     * A day the ledger never saw (the app was not running) returns an empty
     * list rather than 24 zeros, so the caller can tell "no hourly detail" from
     * "an hour in which nobody moved".
     */
    public List<HourEntry> hoursFor(String day, String source) {
        List<HourEntry> out = new ArrayList<>();
        if (day == null || day.isEmpty()) return out;
        SQLiteDatabase db = getReadableDatabase();
        try {
            Cursor cursor = db.query(
                "hours", new String[]{"day", "hour", "source", "steps", "updated_at"},
                "day = ? AND source = ?",
                new String[]{day, source == null ? SOURCE_PHONE : source},
                null, null, "hour ASC", null
            );
            try {
                while (cursor.moveToNext()) {
                    out.add(new HourEntry(
                        cursor.getString(0), cursor.getInt(1), cursor.getString(2),
                        cursor.getInt(3), cursor.getLong(4)
                    ));
                }
            } finally {
                cursor.close();
            }
        } finally {
            db.close();
        }
        return out;
    }

    public void putAnchor(String key, String value) {
        SQLiteDatabase db = getWritableDatabase();
        try {
            ContentValues values = new ContentValues();
            values.put("k", key);
            values.put("v", value);
            db.insertWithOnConflict("anchor", null, values, SQLiteDatabase.CONFLICT_REPLACE);
        } finally {
            db.close();
        }
    }

    public String getAnchor(String key, String fallback) {
        SQLiteDatabase db = getReadableDatabase();
        try {
            Cursor cursor = db.query("anchor", new String[]{"v"}, "k = ?", new String[]{key}, null, null, null);
            try {
                return cursor.moveToFirst() ? cursor.getString(0) : fallback;
            } finally {
                cursor.close();
            }
        } finally {
            db.close();
        }
    }
}
