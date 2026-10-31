package com.jarvisos.app;

import java.util.ArrayDeque;

import org.json.JSONObject;

/**
 * Notices a likely car crash from the accelerometer and GPS while driving: a
 * hard impact at driving speed, then the vehicle coming to a stop and staying
 * there.
 *
 * The impact alone is not enough. A phone dropped into a footwell or thrown
 * onto the seat reads well past 4 g too, and so can a deep pothole, so a
 * candidate only counts once GPS says the car stopped within 20 s of it and
 * stayed stopped for 10 s more. Speed coming back up to a driving pace means
 * the trip went on, and the candidate is dropped.
 *
 * Armed only while driving with a trusted GPS speed of 7 m/s (about 16 mph)
 * or more in the last 10 s: a phone that falls at walking pace or in a parked
 * car is not a crash.
 *
 * Accelerometer samples (m/s², gravity included, as Android gives them) and
 * fixes must share one clock. Fed with plain numbers, so the rules are tested
 * without Android, like {@link DrivingMonitor}.
 */
final class CrashDetector {
    static final double GRAVITY = 9.81;
    /** About 4 g beyond gravity in one sample: an impact on its own. */
    static final double SPIKE_MPS2 = 39.0;
    /** About 2.5 g beyond gravity, held for {@link #SUSTAIN_SAMPLES} samples. */
    static final double SUSTAINED_MPS2 = 25.0;
    static final int SUSTAIN_SAMPLES = 3;
    static final long SUSTAIN_WINDOW_MS = 100L;
    /** About 16 mph, the same speed {@link MotionModeSelector} takes for a vehicle. */
    static final double ARM_MIN_MPS = 7.0;
    static final long ARM_RECENT_MS = 10_000L;
    /** The vehicle must slow below this (about 4.5 mph) within {@link #STOP_WITHIN_MS} of the impact. */
    static final double STOPPED_MPS = 2.0;
    static final long STOP_WITHIN_MS = 20_000L;
    /** And then stay below this (about 6.7 mph) for {@link #HOLD_MS}. */
    static final double STAY_STOPPED_MPS = 3.0;
    static final long HOLD_MS = 10_000L;
    static final int HOLD_MIN_FIXES = 2;
    /**
     * A stop with no fix after it for this long beyond {@link #HOLD_MS} is
     * given up on: what happened then is not known, and a stale candidate
     * should not fire on a fix minutes later.
     */
    static final long HOLD_GRACE_MS = 10_000L;
    /** Back at a driving pace: the trip went on, so it was not a crash. */
    static final double RESUMED_MPS = 7.0;
    /** At most one crash reported per this long. */
    static final long DEBOUNCE_MS = 2 * 60 * 1000L;
    /** A fix less precise than this is a network or poor GPS fix; its speed is not trusted. */
    static final double MAX_ACCURACY_M = 25.0;

    /** A confirmed crash. */
    static final class Result {
        /** Peak acceleration beyond gravity, in g, over the impact and the stop that followed. */
        final double peakG;
        /** The highest trusted speed in the 10 s before the impact, in mph. */
        final double speedBeforeMph;
        /** When the impact happened. */
        final long detectedAtMs;
        /** The fix that confirmed the stop: where the vehicle came to rest. */
        final double lat;
        final double lon;
        /** NaN when the fix had none. */
        final double accuracyM;
        final long positionAtMs;

        Result(double peakG, double speedBeforeMph, long detectedAtMs,
               double lat, double lon, double accuracyM, long positionAtMs) {
            this.peakG = peakG;
            this.speedBeforeMph = speedBeforeMph;
            this.detectedAtMs = detectedAtMs;
            this.lat = lat;
            this.lon = lon;
            this.accuracyM = accuracyM;
            this.positionAtMs = positionAtMs;
        }

        /**
         * The body of {@code POST /api/family/crash}. {@code auto} is false
         * when the person asked for help themselves rather than letting the
         * countdown run out.
         */
        JSONObject toBody(boolean auto) throws Exception {
            JSONObject position = new JSONObject();
            position.put("lat", lat);
            position.put("lon", lon);
            position.put("accuracy", Double.isNaN(accuracyM) ? JSONObject.NULL : round1(accuracyM));
            position.put("at", positionAtMs);
            JSONObject body = new JSONObject();
            body.put("auto", auto);
            body.put("position", position);
            body.put("speed_before_mph", speedBeforeMph);
            body.put("peak_g", peakG);
            body.put("detected_at", detectedAtMs);
            return body;
        }

        /** Kept across a restart of the app's process while the countdown runs. */
        JSONObject toJson() throws Exception {
            JSONObject o = new JSONObject();
            o.put("peak_g", peakG);
            o.put("speed_before_mph", speedBeforeMph);
            o.put("detected_at", detectedAtMs);
            o.put("lat", lat);
            o.put("lon", lon);
            if (!Double.isNaN(accuracyM)) o.put("accuracy", accuracyM);
            o.put("at", positionAtMs);
            return o;
        }

        static Result fromJson(JSONObject o) {
            return new Result(o.optDouble("peak_g", 0), o.optDouble("speed_before_mph", 0),
                    o.optLong("detected_at", 0), o.optDouble("lat", 0), o.optDouble("lon", 0),
                    o.has("accuracy") ? o.optDouble("accuracy", Double.NaN) : Double.NaN, o.optLong("at", 0));
        }
    }

    static double round1(double v) {
        return Math.round(v * 10.0) / 10.0;
    }

    private boolean driving;

    // Trusted speeds of the last ARM_RECENT_MS, oldest first: {time, speed}.
    private final ArrayDeque<double[]> recentSpeeds = new ArrayDeque<>();

    // The current run of samples at SUSTAINED_MPS2 or more: their times, oldest first.
    private final ArrayDeque<Long> strongRun = new ArrayDeque<>();

    // The candidate being confirmed, if any.
    private long impactAt = -1L;
    private double peakMps2;
    private double speedBefore;
    /** When speed first fell below STOPPED_MPS after the impact; -1 until then. */
    private long stopAt = -1L;
    private int holdFixes;

    private long lastReportedAt = Long.MIN_VALUE / 2;

    /** Whether the phone is in driving mode; outside it no new impact is taken. */
    void setDriving(boolean driving) {
        this.driving = driving;
        if (!driving) {
            // Speeds from before a break in driving say nothing about the next drive.
            recentSpeeds.clear();
            strongRun.clear();
        }
    }

    /** Whether an impact is waiting for the stop that would confirm it. */
    boolean isPending() {
        return impactAt >= 0;
    }

    /** One accelerometer sample. Never confirms a crash itself: only a fix can. */
    void onAccel(long timeMs, double x, double y, double z) {
        expire(timeMs);
        double beyond = Math.sqrt(x * x + y * y + z * z) - GRAVITY;
        if (impactAt >= 0) {
            // Later jolts of the same crash (a second hit, the car settling)
            // only raise the peak.
            if (beyond > peakMps2) peakMps2 = beyond;
            return;
        }
        boolean strong = beyond >= SUSTAINED_MPS2;
        if (!strong) {
            strongRun.clear();
        } else {
            strongRun.addLast(timeMs);
            while (!strongRun.isEmpty() && timeMs - strongRun.peekFirst() > SUSTAIN_WINDOW_MS) {
                strongRun.removeFirst();
            }
        }
        boolean impact = beyond >= SPIKE_MPS2 || (strong && strongRun.size() >= SUSTAIN_SAMPLES);
        if (!impact || !armed(timeMs)) return;
        impactAt = timeMs;
        peakMps2 = beyond;
        speedBefore = topRecentSpeed(timeMs);
        stopAt = -1L;
        holdFixes = 0;
        strongRun.clear();
    }

    /**
     * One fix. {@code speedMps} is NaN when it has none; such a fix, and one
     * coarser than {@link #MAX_ACCURACY_M}, neither confirms nor clears a
     * candidate. Returns the crash this fix confirms, or null.
     */
    Result onFix(long timeMs, double speedMps, double accuracyM, double lat, double lon) {
        expire(timeMs);
        boolean trusted = !Double.isNaN(speedMps) && !Double.isNaN(accuracyM) && accuracyM <= MAX_ACCURACY_M;
        if (!trusted) return null;
        if (impactAt < 0) {
            if (driving) {
                recentSpeeds.addLast(new double[]{timeMs, speedMps});
                while (!recentSpeeds.isEmpty() && timeMs - (long) recentSpeeds.peekFirst()[0] > ARM_RECENT_MS) {
                    recentSpeeds.removeFirst();
                }
            }
            return null;
        }
        if (timeMs < impactAt) return null;  // a fix from before the impact, delivered late
        if (speedMps >= RESUMED_MPS) {
            clearCandidate();
            return null;
        }
        if (stopAt < 0) {
            if (speedMps < STOPPED_MPS) {
                stopAt = timeMs;
                holdFixes = 1;
            }
            return null;
        }
        if (speedMps >= STAY_STOPPED_MPS) {
            // Rolling again, though slowly: wait for a new stop, if there is
            // still time for one.
            stopAt = -1L;
            holdFixes = 0;
            if (timeMs - impactAt > STOP_WITHIN_MS) clearCandidate();
            return null;
        }
        holdFixes++;
        if (timeMs - stopAt < HOLD_MS || holdFixes < HOLD_MIN_FIXES) return null;
        Result r = new Result(round1(peakMps2 / GRAVITY), DrivingMonitor.mph(speedBefore), impactAt,
                lat, lon, accuracyM, timeMs);
        lastReportedAt = impactAt;
        clearCandidate();
        recentSpeeds.clear();
        return r;
    }

    /** Drops a candidate that ran out of time for its stop or its confirming fixes. */
    private void expire(long nowMs) {
        if (impactAt < 0) return;
        if (stopAt < 0 && nowMs - impactAt > STOP_WITHIN_MS) {
            clearCandidate();
        } else if (stopAt >= 0 && nowMs - stopAt > HOLD_MS + HOLD_GRACE_MS) {
            clearCandidate();
        }
    }

    private boolean armed(long timeMs) {
        if (!driving || timeMs - lastReportedAt < DEBOUNCE_MS) return false;
        for (double[] s : recentSpeeds) {
            long age = timeMs - (long) s[0];
            if (age >= 0 && age <= ARM_RECENT_MS && s[1] >= ARM_MIN_MPS) return true;
        }
        return false;
    }

    private double topRecentSpeed(long timeMs) {
        double top = 0;
        for (double[] s : recentSpeeds) {
            long age = timeMs - (long) s[0];
            if (age >= 0 && age <= ARM_RECENT_MS && s[1] > top) top = s[1];
        }
        return top;
    }

    private void clearCandidate() {
        impactAt = -1L;
        stopAt = -1L;
        holdFixes = 0;
        peakMps2 = 0;
    }
}
