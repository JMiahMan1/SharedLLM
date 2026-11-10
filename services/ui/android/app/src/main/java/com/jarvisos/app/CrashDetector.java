package com.jarvisos.app;

import java.util.ArrayDeque;

import org.json.JSONArray;
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


    /**
     * About 4 g beyond gravity in one sample: an impact on its own. There is
     * deliberately no softer path (a 2.5 g jolt held for a few samples reads
     * just like a bag dropped in the footwell or a deep pothole), because
     * every false alarm costs a full-screen siren.
     */
    static final double SPIKE_MPS2 = 39.0;
    /**
     * A dropped device falls before it hits: for a moment it reads close to
     * no acceleration at all (free fall), then the spike as it lands. A car
     * crash has no fall before the hit; the device is moving with the car.
     * So a spike right after at least {@link #FREE_FALL_MIN_MS} below
     * {@link #FREE_FALL_MPS2} (about 0.3 g, gravity included) is a drop: a
     * tablet slid off a lap, a phone knocked off the seat. A 15 cm fall
     * already takes about 175 ms.
     */
    static final double FREE_FALL_MPS2 = 3.0;
    static final long FREE_FALL_MIN_MS = 80L;
    /** How soon after the fall ends the landing spike comes. */
    static final long DROP_LANDS_WITHIN_MS = 300L;
    /**
     * A crash "takes the GPS with it" only if there was GPS to take: at least
     * this many trusted fixes in the {@link #GPS_STEADY_MS} before the impact,
     * the newest no older than {@link #GPS_FRESH_MS}. A tablet or a phone in
     * a bag whose GPS was already coming and going says nothing by going quiet
     * after a jolt.
     */
    static final int GPS_STEADY_FIXES = 3;
    static final long GPS_STEADY_MS = 5_000L;
    static final long GPS_FRESH_MS = 2_500L;
    /** Accelerometer kept around an impact: this long before it and after it. */
    static final long TRACE_MS = 1_000L;
    static final int TRACE_MAX = 150;
    /** Fixes (trusted or not) kept from before an impact. */
    static final long FIX_LOG_MS = 10_000L;
    static final int FIX_AFTER_MAX = 60;
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
        /**
         * What the detector saw, for working out a false alarm afterwards:
         * see {@link CrashDetector#diagnostics}. Null on a crash kept by an
         * older build.
         */
        JSONObject diagnostics;

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
            if (diagnostics != null) body.put("diagnostics", diagnostics);
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
            if (diagnostics != null) o.put("diagnostics", diagnostics);
            return o;
        }

        static Result fromJson(JSONObject o) {
            Result r = new Result(o.optDouble("peak_g", 0), o.optDouble("speed_before_mph", 0),
                    o.optLong("detected_at", 0), o.optDouble("lat", 0), o.optDouble("lon", 0),
                    o.has("accuracy") ? o.optDouble("accuracy", Double.NaN) : Double.NaN, o.optLong("at", 0));
            r.diagnostics = o.optJSONObject("diagnostics");
            return r;
        }
    }

    static double round1(double v) {
        return Math.round(v * 10.0) / 10.0;
    }

    private boolean driving;

    // Trusted speeds of the last ARM_RECENT_MS, oldest first: {time, speed}.
    private final ArrayDeque<double[]> recentSpeeds = new ArrayDeque<>();

    // The candidate being confirmed, if any.
    private long impactAt = -1L;
    private double peakMps2;
    private double speedBefore;
    /** When speed first fell below STOPPED_MPS after the impact; -1 until then. */
    private long stopAt = -1L;
    private int holdFixes;

    private long lastReportedAt = Long.MIN_VALUE / 2;

    // The last trusted fix, for a crash whose own fixes never come (below).
    private double lastLat = Double.NaN;
    private double lastLon = Double.NaN;
    private double lastAccuracy = Double.NaN;
    /** Whether any trusted fix has come since the impact. */
    private boolean fixSinceImpact;
    /** Whether the GPS was steady just before the impact (see {@link #GPS_STEADY_FIXES}). */
    private boolean gpsSteadyAtImpact;

    // Times of trusted fixes in the last GPS_STEADY_MS, oldest first, while driving or not.
    private final ArrayDeque<Long> trustedFixTimes = new ArrayDeque<>();

    // Free fall: when the current run below FREE_FALL_MPS2 began (-1: not falling),
    // and when the last long-enough fall ended.
    private long fallStart = -1L;
    private long fallEndedAt = Long.MIN_VALUE / 2;
    private long lastFallMs;

    // For the diagnostics: the last TRACE_MS of {time, magnitude}, every fix of
    // the last FIX_LOG_MS as {time, speed, accuracy}, and what was kept of both
    // around the current candidate.
    private final ArrayDeque<double[]> trace = new ArrayDeque<>();
    private final ArrayDeque<double[]> recentFixes = new ArrayDeque<>();
    private JSONArray traceBefore = new JSONArray();
    private JSONArray traceAfter = new JSONArray();
    private JSONArray fixesBefore = new JSONArray();
    private JSONArray fixesAfter = new JSONArray();
    private double firstMps2;
    private long fallMsAtImpact = -1L;
    private long fallEndedMsBeforeImpact = -1L;
    /** A hard hit that was not reported, and why, until {@link #takeIgnored} collects it. */
    private JSONObject ignored;

    /** Whether the phone is in driving mode; outside it no new impact is taken. */
    void setDriving(boolean driving) {
        this.driving = driving;
        if (!driving) {
            // Speeds from before a break in driving say nothing about the next drive.
            recentSpeeds.clear();
        }
    }

    /** Whether an impact is waiting for the stop that would confirm it. */
    boolean isPending() {
        return impactAt >= 0;
    }

    /**
     * One accelerometer sample. It confirms a crash only in one case: a hard
     * impact (a single {@link #SPIKE_MPS2} spike) after which no trusted fix
     * came at all for {@link #STOP_WITHIN_MS}. A crash can take the GPS with
     * it (the phone thrown, the antenna blocked), and that is the crash most
     * worth reporting; the countdown still lets the person say they are fine.
     * It is reported at the last position known before the impact.
     */
    Result onAccel(long timeMs, double x, double y, double z) {
        Result lost = gpsLost(timeMs);
        if (lost != null) return lost;
        expire(timeMs);
        double magnitude = Math.sqrt(x * x + y * y + z * z);
        noteFall(timeMs, magnitude);
        trace.addLast(new double[]{timeMs, magnitude});
        while (trace.size() > TRACE_MAX || (!trace.isEmpty() && timeMs - (long) trace.peekFirst()[0] > TRACE_MS)) {
            trace.removeFirst();
        }
        double beyond = magnitude - GRAVITY;
        if (impactAt >= 0) {
            if (timeMs - impactAt <= TRACE_MS && traceAfter.length() < TRACE_MAX) {
                traceAfter.put(point(timeMs - impactAt, magnitude));
            }
            // Later jolts of the same crash (a second hit, the car settling)
            // only raise the peak.
            if (beyond > peakMps2) peakMps2 = beyond;
            return null;
        }
        boolean impact = beyond >= SPIKE_MPS2;
        if (!impact || !armed(timeMs)) return null;
        if (timeMs - fallEndedAt <= DROP_LANDS_WITHIN_MS) {
            // It fell, then landed.
            ignored = hitOnly("drop", timeMs, beyond);
            return null;
        }
        gpsSteadyAtImpact = gpsSteady(timeMs);
        impactAt = timeMs;
        peakMps2 = beyond;
        firstMps2 = beyond;
        snapshotBefore(timeMs);
        speedBefore = topRecentSpeed(timeMs);
        stopAt = -1L;
        holdFixes = 0;
        fixSinceImpact = false;
        return null;
    }

    /** The no-fix case described at {@link #onAccel}; null otherwise. */
    private Result gpsLost(long nowMs) {
        if (impactAt < 0 || fixSinceImpact || !gpsSteadyAtImpact || peakMps2 < SPIKE_MPS2) return null;
        if (nowMs - impactAt <= STOP_WITHIN_MS || Double.isNaN(lastLat)) return null;
        Result r = new Result(round1(peakMps2 / GRAVITY), DrivingMonitor.mph(speedBefore), impactAt,
                lastLat, lastLon, lastAccuracy, nowMs);
        r.diagnostics = diagnostics("gps_lost");
        lastReportedAt = impactAt;
        clearCandidate();
        recentSpeeds.clear();
        return r;
    }

    /**
     * One fix. {@code speedMps} is NaN when it has none; such a fix, and one
     * coarser than {@link #MAX_ACCURACY_M}, neither confirms nor clears a
     * candidate. Returns the crash this fix confirms, or null.
     */
    Result onFix(long timeMs, double speedMps, double accuracyM, double lat, double lon) {
        expire(timeMs);
        recentFixes.addLast(new double[]{timeMs, speedMps, accuracyM});
        while (!recentFixes.isEmpty() && timeMs - (long) recentFixes.peekFirst()[0] > FIX_LOG_MS) {
            recentFixes.removeFirst();
        }
        if (impactAt >= 0 && timeMs >= impactAt && fixesAfter.length() < FIX_AFTER_MAX) {
            fixesAfter.put(fixPoint(timeMs - impactAt, speedMps, accuracyM));
        }
        boolean trusted = !Double.isNaN(speedMps) && !Double.isNaN(accuracyM) && accuracyM <= MAX_ACCURACY_M;
        if (!trusted) return null;
        lastLat = lat;
        lastLon = lon;
        lastAccuracy = accuracyM;
        trustedFixTimes.addLast(timeMs);
        while (!trustedFixTimes.isEmpty() && timeMs - trustedFixTimes.peekFirst() > GPS_STEADY_MS) {
            trustedFixTimes.removeFirst();
        }
        if (impactAt >= 0 && timeMs >= impactAt) fixSinceImpact = true;
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
            discard("drove_on");
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
            if (timeMs - impactAt > STOP_WITHIN_MS) discard("rolled_on");
            return null;
        }
        holdFixes++;
        if (timeMs - stopAt < HOLD_MS || holdFixes < HOLD_MIN_FIXES) return null;
        Result r = new Result(round1(peakMps2 / GRAVITY), DrivingMonitor.mph(speedBefore), impactAt,
                lat, lon, accuracyM, timeMs);
        r.diagnostics = diagnostics("stop");
        lastReportedAt = impactAt;
        clearCandidate();
        recentSpeeds.clear();
        return r;
    }

    /** Drops a candidate that ran out of time for its stop or its confirming fixes. */
    private void expire(long nowMs) {
        if (impactAt < 0) return;
        if (stopAt < 0 && nowMs - impactAt > STOP_WITHIN_MS) {
            discard("no_stop");
        } else if (stopAt >= 0 && nowMs - stopAt > HOLD_MS + HOLD_GRACE_MS) {
            discard("stop_unconfirmed");
        }
    }

    /** Tracks runs of free fall; the end of a long-enough run is kept in {@link #fallEndedAt}. */
    private void noteFall(long timeMs, double magnitude) {
        if (magnitude < FREE_FALL_MPS2) {
            if (fallStart < 0) fallStart = timeMs;
            return;
        }
        if (fallStart >= 0 && timeMs - fallStart >= FREE_FALL_MIN_MS) {
            fallEndedAt = timeMs;
            lastFallMs = timeMs - fallStart;
        }
        fallStart = -1L;
    }

    private boolean gpsSteady(long timeMs) {
        int count = 0;
        long newest = Long.MIN_VALUE / 2;
        for (long t : trustedFixTimes) {
            long age = timeMs - t;
            if (age >= 0 && age <= GPS_STEADY_MS) {
                count++;
                if (t > newest) newest = t;
            }
        }
        return count >= GPS_STEADY_FIXES && timeMs - newest <= GPS_FRESH_MS;
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

    /**
     * A hard hit that did not become a crash, once: why ({@code "drop"},
     * {@code "drove_on"}, {@code "no_stop"}, ...) with the same diagnostics a
     * crash carries. Null when there is none. For the false-alarm record, so a
     * family's alarms can be checked against the near misses.
     */
    JSONObject takeIgnored() {
        JSONObject n = ignored;
        ignored = null;
        return n;
    }

    private void discard(String reason) {
        JSONObject d = diagnostics(null);
        try {
            d.put("ignored", reason);
        } catch (Exception ignoredJson) {
            // Diagnostics are best effort.
        }
        ignored = d;
        clearCandidate();
    }

    /** A hit filtered before it became a candidate. */
    private JSONObject hitOnly(String reason, long timeMs, double beyond) {
        JSONObject d = new JSONObject();
        try {
            d.put("ignored", reason);
            d.put("impact_at", timeMs);
            d.put("first_g", round1(beyond / GRAVITY));
            d.put("speed_before_mph", DrivingMonitor.mph(topRecentSpeed(timeMs)));
            d.put("fall_ms", lastFallMs);
            d.put("fall_ended_ms_before", timeMs - fallEndedAt);
            d.put("gps_steady", gpsSteady(timeMs));
            d.put("accel_before", traceSince(timeMs));
            d.put("fixes_before", fixesSince(timeMs));
        } catch (Exception e) {
            // Diagnostics are best effort.
        }
        return d;
    }

    private void snapshotBefore(long timeMs) {
        traceBefore = traceSince(timeMs);
        traceAfter = new JSONArray();
        fixesBefore = fixesSince(timeMs);
        fixesAfter = new JSONArray();
        long sinceFall = timeMs - fallEndedAt;
        fallMsAtImpact = sinceFall <= 5_000L ? lastFallMs : -1L;
        fallEndedMsBeforeImpact = sinceFall <= 5_000L ? sinceFall : -1L;
    }

    private JSONArray traceSince(long timeMs) {
        JSONArray a = new JSONArray();
        for (double[] s : trace) a.put(point((long) s[0] - timeMs, s[1]));
        return a;
    }

    private JSONArray fixesSince(long timeMs) {
        JSONArray a = new JSONArray();
        for (double[] f : recentFixes) a.put(fixPoint((long) f[0] - timeMs, f[1], f[2]));
        return a;
    }

    /** [ms from the impact, magnitude in m/s² with gravity]. */
    private static JSONArray point(long dt, double magnitude) {
        JSONArray p = new JSONArray();
        p.put(dt);
        p.put(Double.isNaN(magnitude) ? JSONObject.NULL : round1(magnitude));
        return p;
    }

    /** [ms from the impact, speed m/s or null, accuracy m or null]. */
    private static JSONArray fixPoint(long dt, double speed, double accuracy) {
        JSONArray p = new JSONArray();
        p.put(dt);
        p.put(Double.isNaN(speed) ? JSONObject.NULL : round1(speed));
        p.put(Double.isNaN(accuracy) ? JSONObject.NULL : round1(accuracy));
        return p;
    }

    /**
     * Everything the decision rested on, for the current candidate:
     * {@code confirmed_by} ("stop" or "gps_lost", null when it was not), the
     * first and peak hit in g, the speed before, whether the GPS was steady,
     * any free fall just before ({@code fall_ms}, -1 for none), the
     * accelerometer from {@link #TRACE_MS} before to after the hit, and every
     * fix from {@link #FIX_LOG_MS} before it to the decision. Times are ms
     * from the impact.
     */
    private JSONObject diagnostics(String confirmedBy) {
        JSONObject d = new JSONObject();
        try {
            d.put("confirmed_by", confirmedBy == null ? JSONObject.NULL : confirmedBy);
            d.put("impact_at", impactAt);
            d.put("first_g", round1(firstMps2 / GRAVITY));
            d.put("peak_g", round1(peakMps2 / GRAVITY));
            d.put("speed_before_mph", DrivingMonitor.mph(speedBefore));
            d.put("gps_steady", gpsSteadyAtImpact);
            d.put("fix_after_impact", fixSinceImpact);
            d.put("stopped_at_ms", stopAt >= 0 ? stopAt - impactAt : -1);
            d.put("hold_fixes", holdFixes);
            d.put("fall_ms", fallMsAtImpact);
            d.put("fall_ended_ms_before", fallEndedMsBeforeImpact);
            d.put("accel_before", traceBefore);
            d.put("accel_after", traceAfter);
            d.put("fixes_before", fixesBefore);
            d.put("fixes_after", fixesAfter);
        } catch (Exception e) {
            // Diagnostics are best effort; the alert never waits on them.
        }
        return d;
    }

    private void clearCandidate() {
        impactAt = -1L;
        stopAt = -1L;
        holdFixes = 0;
        peakMps2 = 0;
        gpsSteadyAtImpact = false;
    }
}
