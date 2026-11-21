package com.jarvisos.app;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * The hub's answer about this phone's request to be unlocked
 * ({@code GET /api/guard/unlock-requests/{id}}): a parent approves or denies it
 * on their own phone, and the lock screen unlocks only on an approval read
 * from the hub for this phone's own request. {@code until} and
 * {@code decided_at} are the hub's, never computed here. Pure, so it is
 * tested on the JVM.
 *
 * <p>The same rules as the family PC (agent/jarvis-guard UnlockRequest.cs):
 * an answer about another request, an approval without the time it was given,
 * one that ends before it began or one longer than {@link #MAX_MINUTES} never
 * unlocks.
 */
final class UnlockAnswer {
    static final String NONE = "none";
    static final String PENDING = "pending";
    static final String APPROVED = "approved";
    static final String DENIED = "denied";
    static final String EXPIRED = "expired";
    static final String SUPERSEDED = "superseded";

    /** The lengths the lock screen offers, and the most a parent can give. */
    static final int[] MINUTE_CHOICES = {15, 30, 60};
    static final int MAX_MINUTES = 240;

    final String status;
    /** Epoch ms; 0 unless approved. */
    final long untilMs;
    final long decidedAtMs;
    final String decidedBy;
    /** The parents' offline-code verifiers (approvals only), or null. */
    final JSONArray offlineCodes;

    private UnlockAnswer(String status, long untilMs, long decidedAtMs, String decidedBy, JSONArray offlineCodes) {
        this.status = status;
        this.untilMs = untilMs;
        this.decidedAtMs = decidedAtMs;
        this.decidedBy = decidedBy;
        this.offlineCodes = offlineCodes;
    }

    /** The id of the request the hub just made; throws when the answer is not one. */
    static long createdId(JSONObject answer) {
        JSONObject request = answer == null ? null : answer.optJSONObject("request");
        if (request == null || !request.has("id")) throw new IllegalArgumentException("The hub's answer has no request id.");
        return request.optLong("id");
    }

    /** The hub's answer about request {@code askedId}; throws when it cannot be trusted. */
    static UnlockAnswer parse(JSONObject answer, long askedId) {
        JSONObject request = answer == null ? null : answer.optJSONObject("request");
        if (request == null) throw new IllegalArgumentException("The hub's answer has no request.");
        if (!request.has("id") || request.optLong("id", -1) != askedId) {
            throw new IllegalArgumentException("The hub answered about another request.");
        }
        String status = request.optString("status", "");
        String by = request.isNull("decided_by") ? null : request.optString("decided_by", null);
        switch (status) {
            case PENDING:
            case DENIED:
            case EXPIRED:
            case SUPERSEDED:
                return new UnlockAnswer(status, 0L, 0L, by, null);
            case APPROVED:
                long decidedAt = epochMs(request, "decided_at");
                long until = epochMs(request, "until");
                if (decidedAt == 0L) throw new IllegalArgumentException("The approval has no time it was given.");
                if (until == 0L) throw new IllegalArgumentException("The approval has no end.");
                if (until <= decidedAt) throw new IllegalArgumentException("The approval ends before it was given.");
                if (until - decidedAt > (MAX_MINUTES + 1) * 60_000L) {
                    throw new IllegalArgumentException("The approval is longer than " + MAX_MINUTES + " minutes.");
                }
                return new UnlockAnswer(APPROVED, until, decidedAt, by, answer.optJSONArray("offline_codes"));
            default:
                throw new IllegalArgumentException("The hub answered an unknown status '" + status + "'.");
        }
    }

    /** What the lock screen says about a request in {@code status}. */
    static String describe(String status, String decidedBy) {
        switch (status) {
            case PENDING:
                return "Asked. Waiting for a parent to answer on their phone…";
            case APPROVED:
                return "A parent said yes.";
            case DENIED:
                return (decidedBy == null || decidedBy.isEmpty() ? "A parent" : decidedBy) + " said no this time.";
            case EXPIRED:
                return "Nobody answered in ten minutes. You can ask again.";
            case SUPERSEDED:
                return "A newer request replaced this one.";
            default:
                return "";
        }
    }

    private static long epochMs(JSONObject request, String name) {
        if (request.isNull(name)) return 0L;
        String iso = request.optString(name, "");
        if (iso.isEmpty()) return 0L;
        try {
            return java.time.OffsetDateTime.parse(iso).toInstant().toEpochMilli();
        } catch (Exception e) {
            return 0L;
        }
    }
}
