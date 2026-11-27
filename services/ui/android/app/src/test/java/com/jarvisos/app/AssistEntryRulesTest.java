package com.jarvisos.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** The ways into the assistant: which button opens a turn, and which never does. */
public class AssistEntryRulesTest {

    @Test
    public void aHeadsetButtonMeansTalkByItself() {
        assertTrue(AssistEntryRules.startsTurn(AssistEntryRules.KEY_HEADSETHOOK, AssistEntryRules.ACTION_UP, false, false));
        assertTrue(AssistEntryRules.startsTurn(AssistEntryRules.KEY_VOICE_ASSIST, AssistEntryRules.ACTION_UP, false, false));
    }

    @Test
    public void aKeyPressOnlyCountsOnceItIsReleased() {
        assertFalse(AssistEntryRules.startsTurn(AssistEntryRules.KEY_HEADSETHOOK, AssistEntryRules.ACTION_DOWN, false, true));
    }

    @Test
    public void playPauseNeedsADoublePressAndTheScreenOn() {
        int key = AssistEntryRules.KEY_MEDIA_PLAY_PAUSE;
        assertTrue(AssistEntryRules.startsTurn(key, AssistEntryRules.ACTION_UP, true, true));
        assertFalse("a single pause is a pause", AssistEntryRules.startsTurn(key, AssistEntryRules.ACTION_UP, false, true));
        assertFalse("never from a pocket", AssistEntryRules.startsTurn(key, AssistEntryRules.ACTION_UP, true, false));
    }

    @Test
    public void aKeyThatMeansNothingElseDoesNothing() {
        assertFalse(AssistEntryRules.startsTurn(4, AssistEntryRules.ACTION_UP, true, true));
    }

    @Test
    public void twoPressesAreOneGestureOnlyWhenTheyAreClose() {
        assertTrue(AssistEntryRules.doublePress(1000, 1400));
        assertFalse(AssistEntryRules.doublePress(1000, 1600));
        assertFalse("no first press yet", AssistEntryRules.doublePress(0, 100));
        assertFalse(AssistEntryRules.doublePress(1000, 900));
    }

    @Test
    public void theAssistantRoleStartsAtAndroidTen() {
        assertFalse(AssistEntryRules.canHoldAssistantRole(28));
        assertTrue(AssistEntryRules.canHoldAssistantRole(29));
    }

    @Test
    public void theRoleIsOfferedOnlyWhenSomeoneElseHasIt() {
        assertTrue(AssistEntryRules.offerAssistantRole(29, false));
        assertFalse(AssistEntryRules.offerAssistantRole(29, true));
        assertFalse(AssistEntryRules.offerAssistantRole(28, false));
    }

    @Test
    public void everyPhoneGetsAWayIn() {
        assertEquals("Press your headset button, or use the Jarvis widget.",
                AssistEntryRules.entrySummary(false, false, true));
        assertEquals("Hold your home button, or use the Jarvis widget.",
                AssistEntryRules.entrySummary(false, true, false));
        assertEquals("Tap the Jarvis widget to talk.", AssistEntryRules.entrySummary(true, false, false));
        assertTrue(AssistEntryRules.entrySummary(false, false, false).contains("widget"));
    }
}
