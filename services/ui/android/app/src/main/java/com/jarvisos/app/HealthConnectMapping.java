package com.jarvisos.app;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.List;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Health Connect records turned into the health hub's ingestion payloads.
 *
 * Pure (no Android or Health Connect types) so every rule is JVM-tested:
 * {@link HealthConnectReader} pulls the plain fields out of each record and
 * hands them here. Every payload carries {@code source: health_connect} and a
 * stable {@code external_id} built from the record's Health Connect id, so the
 * overlapping re-read on each sync is answered "duplicate" by the server
 * instead of adding history twice.
 *
 * Steps are never mapped: the phone's own pedometer already posts them to geo,
 * and a second count from Health Connect would double the day.
 */
public final class HealthConnectMapping {

    public static final String SOURCE = "health_connect";
    /** The first sync reaches back this far (Health Connect's own default history). */
    public static final long FIRST_WINDOW_MS = 30L * 24 * 60 * 60 * 1000;
    /** Each later sync re-reads this much before the last one: late-arriving watch data. */
    public static final long OVERLAP_MS = 48L * 60 * 60 * 1000;

    // SleepSessionRecord.STAGE_TYPE_* (androidx.health.connect 1.1.0).
    public static final int STAGE_UNKNOWN = 0;
    public static final int STAGE_AWAKE = 1;
    public static final int STAGE_SLEEPING = 2;
    public static final int STAGE_OUT_OF_BED = 3;
    public static final int STAGE_LIGHT = 4;
    public static final int STAGE_DEEP = 5;
    public static final int STAGE_REM = 6;
    public static final int STAGE_AWAKE_IN_BED = 7;

    // ExerciseSessionRecord.EXERCISE_TYPE_* that geo has a name for.
    public static final int EXERCISE_BIKING = 8;
    public static final int EXERCISE_BIKING_STATIONARY = 9;
    public static final int EXERCISE_HIKING = 37;
    public static final int EXERCISE_RUNNING = 56;
    public static final int EXERCISE_RUNNING_TREADMILL = 57;
    public static final int EXERCISE_WALKING = 79;

    private HealthConnectMapping() {}

    /** One stage of a sleep session, in epoch millis. */
    public static final class Stage {
        public final int type;
        public final long startMs;
        public final long endMs;

        public Stage(int type, long startMs, long endMs) {
            this.type = type;
            this.startMs = startMs;
            this.endMs = endMs;
        }
    }

    /** Where this sync starts reading: 30 days back the first time, else 48 h before the last. */
    public static long windowStart(long lastSyncMs, long nowMs) {
        long floor = nowMs - FIRST_WINDOW_MS;
        if (lastSyncMs <= 0) return floor;
        return Math.max(floor, Math.min(lastSyncMs, nowMs) - OVERLAP_MS);
    }

    public static String externalId(String kind, String recordId) {
        return "hc:" + kind + ":" + recordId;
    }

    private static ZoneOffset offset(long ms, Integer offsetSeconds) {
        if (offsetSeconds != null) return ZoneOffset.ofTotalSeconds(offsetSeconds);
        return ZoneId.systemDefault().getRules().getOffset(Instant.ofEpochMilli(ms));
    }

    /** ISO-8601 with the offset the record was written in (the phone's zone when absent). */
    public static String isoInstant(long ms, Integer offsetSeconds) {
        return OffsetDateTime.ofInstant(Instant.ofEpochMilli(ms), offset(ms, offsetSeconds))
            .withNano(0)
            .format(DateTimeFormatter.ISO_OFFSET_DATE_TIME);
    }

    /** The local calendar day of an instant, as YYYY-MM-DD. */
    public static String localDay(long ms, Integer offsetSeconds) {
        return OffsetDateTime.ofInstant(Instant.ofEpochMilli(ms), offset(ms, offsetSeconds))
            .toLocalDate()
            .toString();
    }

    private static boolean asleep(int type) {
        return type == STAGE_SLEEPING || type == STAGE_LIGHT || type == STAGE_DEEP || type == STAGE_REM;
    }

    private static String stageName(int type) {
        switch (type) {
            case STAGE_LIGHT: return "light";
            case STAGE_DEEP: return "deep";
            case STAGE_REM: return "rem";
            case STAGE_SLEEPING: return "asleep";
            case STAGE_AWAKE:
            case STAGE_AWAKE_IN_BED: return "awake";
            case STAGE_OUT_OF_BED: return "out_of_bed";
            default: return "unknown";
        }
    }

    /**
     * {@code POST /api/health/sleep} body for one session, or null when it is
     * not a night the store accepts (no length, or longer than a day).
     *
     * The night is the day the session ended (the store's convention). With
     * stages, the duration is the time actually asleep and efficiency is
     * asleep / in bed; without stages it is the whole session and no
     * efficiency is claimed.
     */
    public static JSONObject sleep(String id, long startMs, Integer startOffset, long endMs, Integer endOffset,
                                   List<Stage> stages) throws JSONException {
        long inBedMs = endMs - startMs;
        if (inBedMs <= 0 || inBedMs > 24L * 60 * 60 * 1000) return null;

        JSONObject stageMinutes = new JSONObject();
        long asleepMs = 0;
        if (stages != null) {
            long[] byName = new long[8];
            for (Stage s : stages) {
                long from = Math.max(s.startMs, startMs);
                long to = Math.min(s.endMs, endMs);
                if (to <= from) continue;
                long length = to - from;
                if (asleep(s.type)) asleepMs += length;
                int slot = s.type >= 0 && s.type < byName.length ? s.type : STAGE_UNKNOWN;
                byName[slot] += length;
            }
            for (int type = 0; type < byName.length; type++) {
                if (byName[type] <= 0) continue;
                String name = stageName(type);
                long minutes = Math.round(byName[type] / 60000.0);
                stageMinutes.put(name, stageMinutes.optLong(name, 0) + minutes);
            }
        }

        long durationMinutes = Math.round((asleepMs > 0 ? asleepMs : inBedMs) / 60000.0);
        if (durationMinutes < 1 || durationMinutes > 1440) return null;

        JSONObject out = new JSONObject();
        out.put("night", localDay(endMs, endOffset));
        out.put("bedtime", isoInstant(startMs, startOffset));
        out.put("wake_time", isoInstant(endMs, endOffset));
        out.put("duration_minutes", durationMinutes);
        if (asleepMs > 0) {
            double efficiency = Math.round(asleepMs * 1000.0 / inBedMs) / 10.0;
            out.put("efficiency", Math.min(100.0, efficiency));
        }
        out.put("stages", stageMinutes);
        out.put("source", SOURCE);
        out.put("external_id", externalId("sleep", id));
        return out;
    }

    /** The store's sanity bounds, so one odd reading cannot fail a whole batch. */
    static boolean inBounds(String metric, double value) {
        if (Double.isNaN(value) || Double.isInfinite(value)) return false;
        switch (metric) {
            case "weight": return value >= 1 && value <= 500;
            case "resting_hr": return value >= 20 && value <= 250;
            case "hrv": return value >= 0 && value <= 500;
            default: return false;
        }
    }

    /**
     * One {@code POST /api/health/metrics} sample (resting_hr in bpm, hrv in
     * ms, weight in kg), or null when the value is outside the store's bounds.
     */
    public static JSONObject metric(String metric, String kind, String id, double value, String unit,
                                    long timeMs, Integer offsetSeconds, String origin) throws JSONException {
        if (!inBounds(metric, value)) return null;
        JSONObject out = new JSONObject();
        out.put("metric", metric);
        out.put("value", Math.round(value * 100.0) / 100.0);
        out.put("unit", unit);
        out.put("recorded_at", isoInstant(timeMs, offsetSeconds));
        out.put("source", SOURCE);
        out.put("external_id", externalId(kind, id));
        JSONObject meta = new JSONObject();
        if (origin != null && !origin.isEmpty()) meta.put("origin", origin);
        out.put("metadata", meta);
        return out;
    }

    /** Geo's name for a Health Connect exercise type, or null when geo has none. */
    public static String activityType(int exerciseType) {
        switch (exerciseType) {
            case EXERCISE_WALKING:
            case EXERCISE_HIKING:
                return "walking";
            case EXERCISE_RUNNING:
            case EXERCISE_RUNNING_TREADMILL:
                return "running";
            case EXERCISE_BIKING:
            case EXERCISE_BIKING_STATIONARY:
                return "cycling";
            default:
                return null;
        }
    }

    /**
     * One workout for {@code POST /api/geo/workouts/import}, or null for a type
     * geo cannot name. Times are epoch seconds (geo's convention). No steps:
     * the pedometer owns them.
     */
    public static JSONObject workout(String id, int exerciseType, String title, long startMs, long endMs,
                                     Double distanceMeters, String origin) throws JSONException {
        String type = activityType(exerciseType);
        if (type == null || endMs <= startMs) return null;
        JSONObject out = new JSONObject();
        out.put("external_id", externalId("exercise", id));
        out.put("activity_type", type);
        out.put("start_time", startMs / 1000.0);
        out.put("end_time", endMs / 1000.0);
        if (distanceMeters != null && distanceMeters > 0) out.put("distance_meters", distanceMeters);
        if (title != null && !title.trim().isEmpty()) out.put("title", title.trim());
        if (origin != null && !origin.isEmpty()) out.put("origin", origin);
        return out;
    }
}
