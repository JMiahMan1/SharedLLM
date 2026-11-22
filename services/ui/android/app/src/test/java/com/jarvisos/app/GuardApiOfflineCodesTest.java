package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

/** The offline-code verifiers a status refresh carries replace the kept ones; a missing field keeps them. */
public class GuardApiOfflineCodesTest {

    private static JSONObject verifier(String parent) throws Exception {
        return new JSONObject().put("parent", parent).put("salt", "00").put("iterations", 1000).put("hash", "aa");
    }

    @Test
    public void aRefreshWithVerifiersReplacesTheKeptSet() throws Exception {
        JSONObject status = new JSONObject().put("locked", false)
                .put("offline_codes", new JSONArray().put(verifier("parent-a")).put(verifier("parent-b")));
        JSONArray kept = new JSONArray(GuardApi.offlineCodesToKeep(status));
        assertEquals(2, kept.length());
        assertEquals("parent-a", kept.getJSONObject(0).getString("parent"));
    }

    @Test
    public void anEmptyListClearsThemBecauseNoParentHasACode() throws Exception {
        JSONObject status = new JSONObject().put("offline_codes", new JSONArray());
        assertEquals("[]", GuardApi.offlineCodesToKeep(status));
    }

    @Test
    public void anAnswerWithoutTheFieldKeepsTheLastGoodSet() throws Exception {
        assertNull(GuardApi.offlineCodesToKeep(new JSONObject().put("locked", true)));
        assertNull(GuardApi.offlineCodesToKeep(new JSONObject().put("offline_codes", "not a list")));
        assertNull(GuardApi.offlineCodesToKeep(null));
    }
}
