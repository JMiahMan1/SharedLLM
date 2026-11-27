package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.List;

/** The numbers that decide a spoken turn on the phone. */
public class VoiceTurnRulesTest {

    @Test
    public void a_quiet_room_is_not_speech_but_a_voice_is() {
        assertFalse(VoiceTurnRules.speech(60, 40));
        assertTrue(VoiceTurnRules.speech(1500, 40));
        // A loud room raises the bar, so its own noise is not treated as a question.
        assertFalse(VoiceTurnRules.speech(600, 400));
        assertTrue(VoiceTurnRules.speech(2400, 400));
    }

    @Test
    public void the_floor_follows_the_room_and_stops_at_a_ceiling() {
        assertEquals(50, VoiceTurnRules.nextFloor(40, 120));      // eighth of the way up
        assertEquals(30, VoiceTurnRules.nextFloor(40, 0));        // and down
        assertEquals(VoiceTurnRules.FLOOR_CEILING, VoiceTurnRules.nextFloor(3900, 20000));
    }

    @Test
    public void a_turn_ends_after_speech_and_quiet_never_on_quiet_alone() {
        assertFalse(VoiceTurnRules.endOfTurn(0, 5000, 5000));                       // silence is not a turn
        assertFalse(VoiceTurnRules.endOfTurn(800, 400, 1200));                      // still the comma
        assertTrue(VoiceTurnRules.endOfTurn(800, 750, 1600));
        assertTrue(VoiceTurnRules.endOfTurn(200, 0, VoiceTurnRules.MAX_TURN_MS));    // the cap
    }

    @Test
    public void a_turn_with_hardly_any_speech_is_dropped() {
        assertTrue(VoiceTurnRules.nothingHeard(120));
        assertFalse(VoiceTurnRules.nothingHeard(VoiceTurnRules.MIN_SPEECH_MS));
    }

    @Test
    public void only_sustained_loudness_interrupts_a_reply() {
        assertFalse(VoiceTurnRules.bargeIn(180));
        assertTrue(VoiceTurnRules.bargeIn(VoiceTurnRules.BARGE_IN_MS));
    }

    @Test
    public void the_wake_phrase_can_open_a_question_but_not_appear_inside_one() {
        assertTrue(VoiceTurnRules.wake("Hey Jarvis, what's the weather?", "hey jarvis"));
        assertTrue(VoiceTurnRules.wake("hey jarvis", "Hey Jarvis"));
        assertFalse(VoiceTurnRules.wake("Jarvis said the door was open", "hey jarvis"));
        assertFalse(VoiceTurnRules.wake("", "hey jarvis"));
        assertEquals("what's the weather?", VoiceTurnRules.withoutWake("Hey Jarvis, what's the weather?", "hey jarvis"));
        assertEquals("", VoiceTurnRules.withoutWake("Hey Jarvis", "hey jarvis"));
    }

    @Test
    public void only_a_bare_stop_cuts_the_reply_off() {
        assertTrue(VoiceTurnRules.stopPhrase("Stop!"));
        assertTrue(VoiceTurnRules.stopPhrase("never mind"));
        assertFalse(VoiceTurnRules.stopPhrase("stop the music in the kitchen"));
        assertFalse(VoiceTurnRules.stopPhrase(""));
    }

    @Test
    public void a_reply_is_cut_into_breaths_not_into_fragments() {
        List<String> said = VoiceTurnRules.sentences(
                "The kitchen lamp is on. It has been on since six. Shall I turn it off?", 200);
        assertEquals(3, said.size());
        assertEquals("The kitchen lamp is on.", said.get(0));

        // A short fragment is joined forward so the speech does not stammer.
        assertEquals(1, VoiceTurnRules.sentences("Yes. It is.", 200).size());
        // A decimal point is not a sentence end.
        assertEquals(1, VoiceTurnRules.sentences("It is 3.5 degrees outside.", 200).size());
    }

    @Test
    public void a_very_long_sentence_is_cut_at_a_comma_or_a_space() {
        StringBuilder long_ = new StringBuilder();
        for (int i = 0; i < 30; i++) long_.append("word ");
        List<String> said = VoiceTurnRules.sentences(long_.toString().trim() + ".", 80);
        assertTrue(said.size() > 1);
        for (String piece : said) assertTrue(piece.length() <= 80);
    }

    @Test
    public void every_reason_the_phone_might_stall_has_words_for_it() {
        for (VoiceTurnRules.Ack reason : VoiceTurnRules.Ack.values()) {
            String line = VoiceTurnRules.ack(reason);
            assertFalse("no silence for " + reason, line == null || line.trim().isEmpty());
        }
    }
}
