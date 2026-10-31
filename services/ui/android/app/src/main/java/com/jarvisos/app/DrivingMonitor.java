package com.jarvisos.app;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

import org.json.JSONObject;

/**
 * Driving events noticed on the phone from its GPS fixes while it is in
 * driving mode: hard braking, rapid acceleration, sustained high speed and
 * phone use at speed.
 *
 * GPS only for now. Speed changes are judged between two fixes with speed
 * that are close together (0.5 to 3 s) and both precise (20 m or better);
 * further apart, a stop and a start between them would read as one gentle
 * change, and a coarse fix's speed is noise. 3.3 m/s² (about 7.4 mph a
 * second) is the threshold published for hard braking and rapid acceleration.
 *
 * Fed with plain numbers, so the rules are tested without Android.
 */
final class DrivingMonitor {
    static final double MPS_TO_MPH = 2.2369362920544;
    /** About 7.4 mph gained or lost per second. */
    static final double HARSH_MPS2 = 3.3;
    static final long PAIR_MIN_MS = 500L;
    static final long PAIR_MAX_MS = 3_000L;
    static final double PAIR_MAX_ACCURACY_M = 20.0;
    /** A brake counts only from a real driving speed (about 16 mph), not a crawl. */
    static final double BRAKE_FROM_MIN_MPS = 7.0;
    /** An acceleration counts only once it reaches about 9 mph. */
    static final double ACCEL_TO_MIN_MPS = 4.0;
    /** At most one brake (and one acceleration) per this long: one stop is one event. */
    static final long HARSH_DEBOUNCE_MS = 10_000L;
    /** 80 mph. */
    static final double HIGH_SPEED_MPS = 35.76;
    /** 75 mph: below this a new high speed episode can start. */
    static final double HIGH_SPEED_REARM_MPS = 33.5;
    static final long HIGH_SPEED_SUSTAIN_MS = 10_000L;
    /** About 10 mph: slower is a stop or a car park, where using the phone is fine. */
    static final double PHONE_USE_MIN_MPS = 4.5;
    static final long PHONE_USE_MIN_MS = 15_000L;
    /** Fixes further apart than this break a stretch: what happened between is not known. */
    static final long MAX_GAP_MS = 10_000L;

    /** One noticed event, as posted to /api/activity. */
    static final class Event {
        final String type;
        final long atMs;
        final double lat;
        final double lon;
        final double speedMps;
        /** NaN unless the event is a change of speed. */
        final double fromMps;
        final double toMps;
        /** Negative unless the event lasts (phone use). */
        final long durationMs;

        Event(String type, long atMs, double lat, double lon, double speedMps,
              double fromMps, double toMps, long durationMs) {
            this.type = type;
            this.atMs = atMs;
            this.lat = lat;
            this.lon = lon;
            this.speedMps = speedMps;
            this.fromMps = fromMps;
            this.toMps = toMps;
            this.durationMs = durationMs;
        }

        /** {feature:"driving", event, item:"", detail:{at, lat, lon, speed_mph, from_mph?, to_mph?, duration_s?}} */
        JSONObject toBody() throws Exception {
            JSONObject detail = new JSONObject();
            detail.put("at", atMs);
            detail.put("lat", lat);
            detail.put("lon", lon);
            detail.put("speed_mph", mph(speedMps));
            if (!Double.isNaN(fromMps)) detail.put("from_mph", mph(fromMps));
            if (!Double.isNaN(toMps)) detail.put("to_mph", mph(toMps));
            if (durationMs >= 0) detail.put("duration_s", Math.round(durationMs / 1000.0));
            JSONObject body = new JSONObject();
            body.put("feature", "driving");
            body.put("event", type);
            body.put("item", "");
            body.put("detail", detail);
            return body;
        }
    }

    static double mph(double mps) {
        return Math.round(mps * MPS_TO_MPH * 10.0) / 10.0;
    }

    // The previous fix with a speed, for braking and acceleration.
    private boolean hasPrev;
    private long prevAt;
    private double prevSpeed;
    private double prevAccuracy;
    private long lastBrakeAt = Long.MIN_VALUE / 2;
    private long lastAccelAt = Long.MIN_VALUE / 2;

    // High speed: the start of the current run above 80 mph, and whether this
    // episode was already reported.
    private long highSince = -1L;
    private long highLastAt = -1L;
    private boolean highReported;

    // Phone use: the open stretch, if any.
    private long useSince = -1L;
    private long useLastAt = -1L;
    private double useLat;
    private double useLon;
    private double useTopSpeed;

    /**
     * One fix while driving. {@code speedMps} is NaN when the fix has none.
     * Returns the events it completes, usually none.
     */
    List<Event> onFix(long timeMs, double speedMps, double accuracyM, double lat, double lon,
                      boolean screenInteractive) {
        List<Event> out = new ArrayList<>();
        boolean hasSpeed = !Double.isNaN(speedMps);
        boolean precise = !Double.isNaN(accuracyM) && accuracyM <= PAIR_MAX_ACCURACY_M;

        if (hasSpeed && (!hasPrev || timeMs > prevAt)) {
            if (hasPrev && precise && prevAccuracy <= PAIR_MAX_ACCURACY_M) {
                long dt = timeMs - prevAt;
                if (dt >= PAIR_MIN_MS && dt <= PAIR_MAX_MS) {
                    double rate = (speedMps - prevSpeed) / (dt / 1000.0);
                    if (-rate >= HARSH_MPS2 && prevSpeed >= BRAKE_FROM_MIN_MPS
                            && timeMs - lastBrakeAt >= HARSH_DEBOUNCE_MS) {
                        lastBrakeAt = timeMs;
                        out.add(new Event("hard_brake", timeMs, lat, lon, speedMps, prevSpeed, speedMps, -1L));
                    } else if (rate >= HARSH_MPS2 && speedMps >= ACCEL_TO_MIN_MPS
                            && timeMs - lastAccelAt >= HARSH_DEBOUNCE_MS) {
                        lastAccelAt = timeMs;
                        out.add(new Event("rapid_accel", timeMs, lat, lon, speedMps, prevSpeed, speedMps, -1L));
                    }
                }
            }
            hasPrev = true;
            prevAt = timeMs;
            prevSpeed = speedMps;
            prevAccuracy = Double.isNaN(accuracyM) ? Double.MAX_VALUE : accuracyM;
        }

        // A coarse fix's speed can spike well past the real one; it neither
        // counts toward an episode nor ends one.
        if (hasSpeed && precise) {
            if (speedMps < HIGH_SPEED_REARM_MPS) highReported = false;
            if (speedMps >= HIGH_SPEED_MPS) {
                if (highSince < 0 || timeMs - highLastAt > MAX_GAP_MS) highSince = timeMs;
                highLastAt = timeMs;
                if (!highReported && timeMs - highSince >= HIGH_SPEED_SUSTAIN_MS) {
                    highReported = true;
                    out.add(new Event("high_speed", timeMs, lat, lon, speedMps, Double.NaN, Double.NaN,
                            timeMs - highSince));
                }
            } else {
                highSince = -1L;
            }
        }

        // Phone use: the screen on while moving. A fix with no speed carries an
        // open stretch along but cannot start one.
        boolean gap = useSince >= 0 && timeMs - useLastAt > MAX_GAP_MS;
        boolean moving = hasSpeed ? speedMps >= PHONE_USE_MIN_MPS : useSince >= 0;
        if (useSince >= 0 && (gap || !screenInteractive || !moving)) {
            Event e = closeUse();
            if (e != null) out.add(e);
        }
        // Again after a close: a fix with no speed cannot start the next one.
        moving = hasSpeed ? speedMps >= PHONE_USE_MIN_MPS : useSince >= 0;
        if (screenInteractive && moving && (useSince < 0 || timeMs >= useLastAt)) {
            if (useSince < 0) {
                useSince = timeMs;
                useLat = lat;
                useLon = lon;
                useTopSpeed = hasSpeed ? speedMps : 0;
            } else if (hasSpeed && speedMps > useTopSpeed) {
                useTopSpeed = speedMps;
            }
            useLastAt = timeMs;
        }
        return out;
    }

    /**
     * Driving has ended (or the service is stopping): close any open phone use
     * stretch and forget the rest, so the next drive starts clean.
     */
    List<Event> finish() {
        Event e = closeUse();
        hasPrev = false;
        highSince = -1L;
        highLastAt = -1L;
        highReported = false;
        return e == null ? Collections.emptyList() : Collections.singletonList(e);
    }

    /** The open stretch as an event when it lasted long enough; either way it ends. */
    private Event closeUse() {
        if (useSince < 0) return null;
        long duration = useLastAt - useSince;
        Event e = duration >= PHONE_USE_MIN_MS
                ? new Event("phone_use", useSince, useLat, useLon, useTopSpeed, Double.NaN, Double.NaN, duration)
                : null;
        useSince = -1L;
        useLastAt = -1L;
        return e;
    }
}
