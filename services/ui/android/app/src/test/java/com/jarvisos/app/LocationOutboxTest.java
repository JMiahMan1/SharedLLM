package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

public class LocationOutboxTest {

    /** The queue in memory, as SharedPreferences would hold it (a JSON string). */
    private static final class MemoryStore implements LocationOutbox.Store {
        String json = "[]";

        @Override
        public JSONArray load() {
            try {
                return new JSONArray(json);
            } catch (JSONException e) {
                throw new AssertionError(e);
            }
        }

        @Override
        public void save(JSONArray pending) {
            json = pending.toString();
        }

        int size() {
            return load().length();
        }

        double time(int i) {
            return load().optJSONObject(i).optJSONObject("body").optDouble("timestamp");
        }
    }

    /** Answers with the scripted outcomes in turn (then OK), and records what it was sent. */
    private static final class ScriptedSender implements LocationOutbox.Sender {
        final Deque<LocationOutbox.Sent> script = new ArrayDeque<>();
        final List<Double> sent = new ArrayList<>();

        ScriptedSender then(LocationOutbox.Sent... outcomes) {
            for (LocationOutbox.Sent s : outcomes) script.add(s);
            return this;
        }

        @Override
        public LocationOutbox.Sent send(JSONObject body) {
            sent.add(body.optDouble("timestamp"));
            return script.isEmpty() ? LocationOutbox.Sent.OK : script.poll();
        }
    }

    private static JSONObject fix(double timestamp) {
        try {
            JSONObject body = new JSONObject();
            body.put("latitude", 39.69 + timestamp / 1e6);
            body.put("longitude", -104.92);
            body.put("timestamp", timestamp);
            return body;
        } catch (JSONException e) {
            throw new AssertionError(e);
        }
    }

    @Test
    public void statusesSortIntoTakenRetryRefusedAndSignedOut() {
        assertEquals(LocationOutbox.Sent.OK, LocationOutbox.classify(200));
        assertEquals(LocationOutbox.Sent.OK, LocationOutbox.classify(204));
        assertEquals(LocationOutbox.Sent.RETRY, LocationOutbox.classify(500));
        assertEquals(LocationOutbox.Sent.RETRY, LocationOutbox.classify(502));
        assertEquals(LocationOutbox.Sent.RETRY, LocationOutbox.classify(503));
        assertEquals(LocationOutbox.Sent.RETRY, LocationOutbox.classify(408));
        assertEquals(LocationOutbox.Sent.RETRY, LocationOutbox.classify(429));
        assertEquals(LocationOutbox.Sent.SIGNED_OUT, LocationOutbox.classify(401));
        assertEquals(LocationOutbox.Sent.SIGNED_OUT, LocationOutbox.classify(403));
        assertEquals(LocationOutbox.Sent.REFUSED, LocationOutbox.classify(404));
        assertEquals(LocationOutbox.Sent.REFUSED, LocationOutbox.classify(422));
        assertEquals(LocationOutbox.Sent.REFUSED, LocationOutbox.classify(400));
    }

    @Test
    public void aLiveFixThatCannotBeSentNowIsKeptWithItsOwnTime() {
        MemoryStore store = new MemoryStore();
        LocationOutbox.Sent result = LocationOutbox.sendLive(store, "alex", fix(1000.5),
            new ScriptedSender().then(LocationOutbox.Sent.RETRY));
        assertEquals(LocationOutbox.Sent.RETRY, result);
        assertEquals(1, store.size());
        assertEquals(1000.5, store.time(0), 0.0);
        assertEquals("alex", store.load().optJSONObject(0).optString("user"));
    }

    @Test
    public void aRefusedLiveFixIsDroppedNotKept() {
        for (LocationOutbox.Sent refusal : new LocationOutbox.Sent[] {
                LocationOutbox.Sent.REFUSED, LocationOutbox.Sent.SIGNED_OUT}) {
            MemoryStore store = new MemoryStore();
            LocationOutbox.Sent result = LocationOutbox.sendLive(store, "alex", fix(1000),
                new ScriptedSender().then(refusal));
            assertEquals(refusal, result);
            assertEquals(0, store.size());
        }
    }

    @Test
    public void aSentLiveFixIsNotKept() {
        MemoryStore store = new MemoryStore();
        assertEquals(LocationOutbox.Sent.OK,
            LocationOutbox.sendLive(store, "alex", fix(1000), new ScriptedSender()));
        assertEquals(0, store.size());
    }

    @Test
    public void theSameFixIsKeptOnce() {
        MemoryStore store = new MemoryStore();
        LocationOutbox.add(store, "alex", fix(1000));
        LocationOutbox.add(store, "alex", fix(1000));
        LocationOutbox.add(store, "alex", fix(1001));
        assertEquals(2, store.size());
    }

    @Test
    public void aFullQueueDropsItsOldestFix() {
        MemoryStore store = new MemoryStore();
        for (int i = 0; i < LocationOutbox.MAX_PENDING + 3; i++) {
            LocationOutbox.add(store, "alex", fix(i));
        }
        assertEquals(LocationOutbox.MAX_PENDING, store.size());
        assertEquals(3.0, store.time(0), 0.0);
        assertEquals(LocationOutbox.MAX_PENDING + 2, store.time(LocationOutbox.MAX_PENDING - 1), 0.0);
    }

    @Test
    public void theBacklogIsSentOldestFirstAndAheadOfTheLiveFix() {
        MemoryStore store = new MemoryStore();
        LocationOutbox.add(store, "alex", fix(1));
        LocationOutbox.add(store, "alex", fix(2));
        LocationOutbox.add(store, "alex", fix(3));
        ScriptedSender sender = new ScriptedSender();
        assertEquals(LocationOutbox.Sent.OK, LocationOutbox.sendLive(store, "alex", fix(4), sender));
        assertEquals(List.of(1.0, 2.0, 3.0, 4.0), sender.sent);
        assertEquals(0, store.size());
    }

    @Test
    public void aFlushStopsAtTheFirstFailureAndKeepsTheRestInOrder() {
        MemoryStore store = new MemoryStore();
        for (int i = 1; i <= 4; i++) LocationOutbox.add(store, "alex", fix(i));
        ScriptedSender sender = new ScriptedSender()
            .then(LocationOutbox.Sent.OK, LocationOutbox.Sent.RETRY);
        assertEquals(LocationOutbox.Flushed.RETRY,
            LocationOutbox.flush(store, "alex", sender, LocationOutbox.FLUSH_LIMIT));
        // Fix 3 and 4 were never tried; 2 failed and is kept at the front.
        assertEquals(List.of(1.0, 2.0), sender.sent);
        assertEquals(3, store.size());
        assertEquals(2.0, store.time(0), 0.0);
        assertEquals(4.0, store.time(2), 0.0);
    }

    @Test
    public void aLiveFixWaitsBehindABacklogThatCannotBeSent() {
        MemoryStore store = new MemoryStore();
        LocationOutbox.add(store, "alex", fix(1));
        ScriptedSender sender = new ScriptedSender().then(LocationOutbox.Sent.RETRY);
        assertEquals(LocationOutbox.Sent.RETRY, LocationOutbox.sendLive(store, "alex", fix(2), sender));
        // The live fix was not tried out of order; it joined the queue behind fix 1.
        assertEquals(List.of(1.0), sender.sent);
        assertEquals(2, store.size());
        assertEquals(2.0, store.time(1), 0.0);
    }

    @Test
    public void aRefusedKeyStopsTheFlushAndKeepsEverything() {
        MemoryStore store = new MemoryStore();
        LocationOutbox.add(store, "alex", fix(1));
        LocationOutbox.add(store, "alex", fix(2));
        ScriptedSender sender = new ScriptedSender().then(LocationOutbox.Sent.SIGNED_OUT);
        assertEquals(LocationOutbox.Sent.SIGNED_OUT, LocationOutbox.sendLive(store, "alex", fix(3), sender));
        // One request, not one per fix, and the backlog waits for a key that works.
        assertEquals(1, sender.sent.size());
        assertEquals(2, store.size());
    }

    @Test
    public void aFixTheServerRefusesLeavesTheQueueAndTheFlushGoesOn() {
        MemoryStore store = new MemoryStore();
        for (int i = 1; i <= 3; i++) LocationOutbox.add(store, "alex", fix(i));
        ScriptedSender sender = new ScriptedSender()
            .then(LocationOutbox.Sent.OK, LocationOutbox.Sent.REFUSED, LocationOutbox.Sent.OK);
        assertEquals(LocationOutbox.Flushed.EMPTY,
            LocationOutbox.flush(store, "alex", sender, LocationOutbox.FLUSH_LIMIT));
        assertEquals(List.of(1.0, 2.0, 3.0), sender.sent);
        assertEquals(0, store.size());
    }

    @Test
    public void aFixLeavesTheQueueOnlyAfterTheServerTookIt() {
        MemoryStore store = new MemoryStore();
        LocationOutbox.add(store, "alex", fix(1));
        LocationOutbox.add(store, "alex", fix(2));
        List<Integer> sizesWhileSending = new ArrayList<>();
        LocationOutbox.Sender sender = body -> {
            sizesWhileSending.add(store.size());
            return LocationOutbox.Sent.OK;
        };
        LocationOutbox.flush(store, "alex", sender, LocationOutbox.FLUSH_LIMIT);
        // Still on disk while being sent, so a crash mid-send loses nothing.
        assertEquals(List.of(2, 2), sizesWhileSending);
        assertEquals(0, store.size());
    }

    @Test
    public void aFixAddedDuringAFlushIsNotLost() {
        MemoryStore store = new MemoryStore();
        LocationOutbox.add(store, "alex", fix(1));
        LocationOutbox.Sender sender = body -> {
            LocationOutbox.add(store, "alex", fix(9));
            return LocationOutbox.Sent.OK;
        };
        LocationOutbox.flush(store, "alex", sender, LocationOutbox.FLUSH_LIMIT);
        assertEquals(1, store.size());
        assertEquals(9.0, store.time(0), 0.0);
    }

    @Test
    public void oneFlushSendsAtMostItsLimit() {
        MemoryStore store = new MemoryStore();
        for (int i = 0; i < 5; i++) LocationOutbox.add(store, "alex", fix(i));
        ScriptedSender sender = new ScriptedSender();
        assertEquals(LocationOutbox.Flushed.MORE, LocationOutbox.flush(store, "alex", sender, 3));
        assertEquals(3, sender.sent.size());
        assertEquals(2, store.size());
        assertEquals(3.0, store.time(0), 0.0);
        assertEquals(LocationOutbox.Flushed.EMPTY, LocationOutbox.flush(store, "alex", sender, 3));
        assertFalse(LocationOutbox.hasPending(store));
    }

    @Test
    public void fixesKeptForSomeoneElseAreDroppedUnsent() {
        // Another person signed in on this phone: their history is not this one's.
        MemoryStore store = new MemoryStore();
        LocationOutbox.add(store, "previous", fix(1));
        LocationOutbox.add(store, "alex", fix(2));
        ScriptedSender sender = new ScriptedSender();
        assertEquals(LocationOutbox.Flushed.EMPTY,
            LocationOutbox.flush(store, "alex", sender, LocationOutbox.FLUSH_LIMIT));
        assertEquals(List.of(2.0), sender.sent);
        assertEquals(0, store.size());
    }
}
