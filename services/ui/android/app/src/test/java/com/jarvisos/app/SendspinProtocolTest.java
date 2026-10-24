package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public class SendspinProtocolTest {

    @Test
    public void helloAnnouncesTheSamePlayerWithPcmOnly() throws Exception {
        JSONObject hello = SendspinProtocol.hello("4024", "Jeremiah's Web Player (Mobile)", "Google", "17");
        assertEquals("client/hello", hello.getString("type"));
        JSONObject p = hello.getJSONObject("payload");
        assertEquals("4024", p.getString("client_id"));
        assertEquals("Jeremiah's Web Player (Mobile)", p.getString("name"));
        assertEquals("player@v1", p.getJSONArray("supported_roles").getString(0));
        JSONObject formats = p.getJSONObject("player@v1_support").getJSONArray("supported_formats").getJSONObject(0);
        assertEquals("pcm", formats.getString("codec"));
        assertEquals(16, formats.getInt("bit_depth"));
    }

    @Test
    public void stateAndCommandsUseTheProtocolShapes() throws Exception {
        JSONObject state = SendspinProtocol.state(true, 70, false);
        assertEquals("synchronized", state.getJSONObject("payload").getJSONObject("player").getString("state"));
        assertEquals(70, state.getJSONObject("payload").getJSONObject("player").getInt("volume"));
        assertEquals("error", SendspinProtocol.state(false, 0, true).getJSONObject("payload").getJSONObject("player").getString("state"));

        JSONObject next = SendspinProtocol.command("next");
        assertEquals("client/command", next.getString("type"));
        assertEquals("next", next.getJSONObject("payload").getJSONObject("controller").getString("command"));

        assertEquals(1234L, SendspinProtocol.time(1234L).getJSONObject("payload").getLong("client_transmitted"));
    }

    @Test
    public void anAudioChunkCarriesItsServerTimeAndSamples() {
        byte[] msg = {4, 0, 0, 0, 0, 0, 0, 0x01, 0x02, 9, 8, 7, 6};
        SendspinProtocol.Chunk c = SendspinProtocol.parseChunk(msg);
        assertNotNull(c);
        assertEquals(258L, c.serverTimeUs);
        assertEquals(4, c.length());
        assertEquals(9, c.data[c.offset]);
    }

    @Test
    public void otherBinaryMessagesAreNotAudio() {
        assertNull(SendspinProtocol.parseChunk(new byte[] {8, 0, 0, 0, 0, 0, 0, 0, 1, 2}));
        assertNull(SendspinProtocol.parseChunk(new byte[] {4, 0, 0}));
        assertNull(SendspinProtocol.parseChunk(null));
    }

    @Test
    public void onlySixteenBitPcmIsPlayable() throws Exception {
        JSONObject start = new JSONObject().put("player", new JSONObject()
                .put("codec", "pcm").put("sample_rate", 48000).put("channels", 2).put("bit_depth", 16));
        SendspinProtocol.Format f = SendspinProtocol.Format.from(start);
        assertTrue(f.playable());
        assertEquals(192000, f.bytesPerSecond());

        JSONObject flac = new JSONObject().put("player", new JSONObject()
                .put("codec", "flac").put("sample_rate", 48000).put("channels", 2).put("bit_depth", 16));
        assertFalse(SendspinProtocol.Format.from(flac).playable());
        assertNull(SendspinProtocol.Format.from(new JSONObject()));
    }

    @Test
    public void theShortestRoundTripSetsTheClockOffset() {
        SendspinProtocol.ClockSync sync = new SendspinProtocol.ClockSync();
        assertFalse(sync.synced());
        // Server clock runs 1,000,000 us ahead. A slow probe (40 ms round trip, asymmetric)...
        sync.add(0, 1_030_000, 1_030_000, 40_000);
        // ...then a fast, symmetric one (2 ms): it wins.
        sync.add(100_000, 1_101_000, 1_101_000, 102_000);
        assertTrue(sync.synced());
        assertEquals(500_000L, sync.toLocalUs(1_500_000));

        // A slower probe in the same burst does not replace it.
        sync.add(200_000, 1_250_000, 1_250_000, 260_000);
        assertEquals(500_000L, sync.toLocalUs(1_500_000));
    }
}
