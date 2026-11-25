package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

public class NearbyRulesTest {
    private static NearbyRules.Parts parts(boolean nearby, boolean chat, boolean offline, boolean car, boolean child) {
        return new NearbyRules.Parts(nearby, chat, offline, car, child);
    }

    private static final NearbyRules.Parts NONE = parts(false, false, false, false, false);

    @Test
    public void theRadioRunsForAnyPartButTheCar() {
        assertFalse(NONE.engine());
        assertFalse(parts(false, false, false, true, false).engine());
        assertTrue(parts(true, false, false, false, false).engine());
        assertTrue(parts(false, true, false, false, false).engine());
        assertTrue(parts(false, false, true, false, false).engine());
    }

    @Test
    public void aChildsParentLinkIsAlwaysOn() {
        NearbyRules.Parts child = parts(false, false, false, false, true);
        assertTrue(child.engine());
        assertTrue(child.mesh());
        // The link alone records nobody and fetches nothing.
        assertFalse(child.sightings());
        assertFalse(child.snapshot());
    }

    @Test
    public void onlyFindFamilyNearbyRecordsSightingsAndOnlyOfflineModeFetchesTheSnapshot() {
        assertFalse(parts(false, true, true, true, false).sightings());
        assertTrue(parts(true, false, false, false, false).sightings());
        assertFalse(parts(true, true, false, false, false).snapshot());
        assertTrue(parts(false, false, true, false, false).snapshot());
        // Find family nearby needs no phone-to-phone syncs.
        assertFalse(parts(true, false, false, true, false).mesh());
    }

    @Test
    public void theEngineRidesOnLocationSharingWhenItRunsElseItsOwnService() {
        assertEquals(NearbyRules.Host.LOCATION, NearbyRules.host(true, true, true));
        assertEquals(NearbyRules.Host.OWN, NearbyRules.host(true, true, false));
        assertEquals(NearbyRules.Host.NONE, NearbyRules.host(false, true, true));
        assertEquals(NearbyRules.Host.NONE, NearbyRules.host(false, true, false));
        // No scan permission: nothing to run, so no notification either.
        assertEquals(NearbyRules.Host.NONE, NearbyRules.host(true, false, false));
    }

    @Test
    public void permissionsAreAskedOnlyForAPartJustTurnedOn() {
        // Find family nearby: scan and advertise, no connect.
        assertEquals(Arrays.asList("scan", "advertise"),
                NearbyRules.toAsk(NONE, parts(true, false, false, false, false), 34, false, false, false));
        // The chat: connect too.
        assertEquals(Arrays.asList("scan", "advertise", "connect"),
                NearbyRules.toAsk(NONE, parts(false, true, false, false, false), 34, false, false, false));
        // Car detection: connect only.
        assertEquals(Collections.singletonList("connect"),
                NearbyRules.toAsk(NONE, parts(false, false, false, true, false), 34, false, false, false));
        // Already granted: nothing.
        assertEquals(Collections.emptyList(),
                NearbyRules.toAsk(NONE, parts(true, true, true, true, false), 34, true, true, true));
        // Turning a part off asks nothing.
        assertEquals(Collections.emptyList(),
                NearbyRules.toAsk(parts(true, true, false, false, false), parts(true, false, false, false, false),
                        34, true, true, false));
        // Unchanged parts ask nothing, even with a permission missing.
        NearbyRules.Parts same = parts(true, false, false, false, false);
        assertEquals(Collections.emptyList(), NearbyRules.toAsk(same, same, 34, false, false, false));
    }

    @Test
    public void android9To11AskNothingNew() {
        assertEquals(Collections.emptyList(),
                NearbyRules.toAsk(NONE, parts(true, true, true, true, false), 28, false, false, false));
        assertEquals(Collections.emptyList(),
                NearbyRules.toAsk(NONE, parts(true, true, true, true, false), 30, false, false, false));
    }

    @Test
    public void theServersChoiceIsMergedOverThePhones() throws Exception {
        NearbyRules.Parts phone = parts(true, false, false, true, false);
        NearbyRules.Parts merged = NearbyRules.merge(phone, new JSONObject().put("chat", true).put("car", false), false);
        assertTrue(merged.nearby);
        assertTrue(merged.chat);
        assertFalse(merged.offline);
        assertFalse(merged.car);
        // A value that is not true/false keeps the phone's.
        assertTrue(NearbyRules.merge(phone, new JSONObject().put("nearby", "no"), false).nearby);
        // The child flag comes from the phone (Guardian), never from the server's switches.
        assertTrue(NearbyRules.merge(phone, new JSONObject().put("parentLink", false), true).child);
        assertTrue(NearbyRules.merge(phone, null, true).child);
    }

    @Test
    public void offlineModeFollowsTheOldChatSwitchUntilChosen() {
        assertTrue(NearbyRules.migratedOffline(false, false, true));
        assertFalse(NearbyRules.migratedOffline(false, false, false));
        assertFalse(NearbyRules.migratedOffline(false, true, true));
        assertTrue(NearbyRules.migratedOffline(true, true, false));
    }

    @Test
    public void theStateSaysWhichPartsAreOn() throws Exception {
        JSONObject o = parts(true, false, true, false, true).toJson();
        assertTrue(o.getBoolean("nearby"));
        assertFalse(o.getBoolean("chat"));
        assertTrue(o.getBoolean("offline"));
        assertFalse(o.getBoolean("car"));
        assertTrue(o.getBoolean("parentLink"));
    }
}
