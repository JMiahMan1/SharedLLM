package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;

import org.json.JSONObject;
import org.junit.Test;

/** The lock screen unlocks only on the hub's approval of this phone's own request. */
public class UnlockAnswerTest {

    private static final String DECIDED = "2026-01-05T20:00:00.123456+00:00";
    private static final long T0 = java.time.OffsetDateTime.parse(DECIDED).toInstant().toEpochMilli();

    private static JSONObject answer(String request, String extra) throws Exception {
        return new JSONObject("{\"request\":{" + request + "}" + (extra.isEmpty() ? "" : "," + extra) + "}");
    }

    @Test
    public void anApprovalCarriesTheHubsTimesAndTheVerifiers() throws Exception {
        UnlockAnswer a = UnlockAnswer.parse(answer(
                "\"id\":12,\"status\":\"approved\",\"decided_by\":\"parent-a\",\"decided_at\":\"" + DECIDED
                        + "\",\"until\":\"2026-01-05T20:30:00.123456+00:00\"",
                "\"offline_codes\":[{\"parent\":\"parent-a\",\"salt\":\"00\",\"iterations\":1000,\"hash\":\"aa\"}]"), 12);
        assertEquals(UnlockAnswer.APPROVED, a.status);
        assertEquals(T0, a.decidedAtMs);
        assertEquals(T0 + 30 * 60_000L, a.untilMs);
        assertEquals("parent-a", a.decidedBy);
        assertNotNull(a.offlineCodes);
        assertEquals("parent-a", a.offlineCodes.getJSONObject(0).getString("parent"));
    }

    @Test
    public void everyOtherAnswerNeverUnlocks() throws Exception {
        for (String status : new String[] {"pending", "denied", "expired", "superseded"}) {
            UnlockAnswer a = UnlockAnswer.parse(answer(
                    "\"id\":12,\"status\":\"" + status + "\",\"until\":\"2026-01-05T21:00:00+00:00\"", ""), 12);
            assertEquals(status, a.status);
            assertEquals(0L, a.untilMs);
            assertNull(a.offlineCodes);
        }
    }

    @Test
    public void anAnswerAboutAnotherRequestIsRefused() throws Exception {
        JSONObject other = answer("\"id\":13,\"status\":\"approved\",\"decided_at\":\"" + DECIDED
                + "\",\"until\":\"2026-01-05T20:30:00+00:00\"", "");
        assertThrows(IllegalArgumentException.class, () -> UnlockAnswer.parse(other, 12));
    }

    @Test
    public void anApprovalThePhoneCannotTrustNeverUnlocks() throws Exception {
        String[] bad = {
            "\"until\":\"2026-01-05T20:30:00+00:00\"",
            "\"decided_at\":\"2026-01-05T20:00:00+00:00\"",
            "\"decided_at\":\"2026-01-05T20:00:00+00:00\",\"until\":\"2026-01-05T19:59:00+00:00\"",
            "\"decided_at\":\"2026-01-05T20:00:00+00:00\",\"until\":\"2026-01-06T20:00:00+00:00\"",
        };
        for (String times : bad) {
            JSONObject json = answer("\"id\":12,\"status\":\"approved\"," + times, "");
            assertThrows(times, IllegalArgumentException.class, () -> UnlockAnswer.parse(json, 12));
        }
    }

    @Test
    public void anUnknownStatusOrAMissingRequestIsAnError() throws Exception {
        JSONObject maybe = answer("\"id\":12,\"status\":\"maybe\"", "");
        assertThrows(IllegalArgumentException.class, () -> UnlockAnswer.parse(maybe, 12));
        assertThrows(IllegalArgumentException.class, () -> UnlockAnswer.parse(new JSONObject("{\"detail\":\"x\"}"), 12));
        assertThrows(IllegalArgumentException.class, () -> UnlockAnswer.createdId(new JSONObject("{}")));
        assertEquals(7L, UnlockAnswer.createdId(new JSONObject("{\"request\":{\"id\":7}}")));
    }

    @Test
    public void theLockScreenSaysWhoSaidNo() {
        assertEquals("parent-a said no this time.", UnlockAnswer.describe(UnlockAnswer.DENIED, "parent-a"));
        assertEquals("A parent said no this time.", UnlockAnswer.describe(UnlockAnswer.DENIED, null));
        for (int m : UnlockAnswer.MINUTE_CHOICES) {
            assertEquals(true, m >= 5 && m <= UnlockAnswer.MAX_MINUTES);
        }
    }
}
