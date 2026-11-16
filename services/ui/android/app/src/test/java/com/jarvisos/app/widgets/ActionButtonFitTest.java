package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** Desktop-JVM tests for how the action button fills its widget, like an app icon. */
public class ActionButtonFitTest {

    @Test
    public void theButtonFillsMostOfAOneCellWidget() {
        ActionButtonFit f = ActionButtonFit.of(72, 88, "Garage", "Closed");
        assertTrue(f.label);
        // Most of the cell's width is the button, and the glyph most of the button.
        assertTrue(f.button >= 0.7f * 72);
        assertTrue(f.glyphPad * 2 <= f.button * 0.4f);
    }

    @Test
    public void theNameIsNeverCutItWrapsThenShrinks() {
        // At a one-cell size every name shows: two lines at 12sp, else up to three at 10sp.
        for (String name : new String[] {"Office lamp", "Living room ceiling fan", "Christmas lights outside"}) {
            ActionButtonFit f = ActionButtonFit.of(72, 88, name, "On");
            assertTrue(name, f.label);
            assertTrue(name, f.lines <= (f.smallText ? ActionButtonFit.MAX_SMALL_LINES : ActionButtonFit.MAX_LINES));
            assertTrue(name, f.button >= ActionButtonFit.MIN_WITH_LABEL);
        }
        ActionButtonFit longName = ActionButtonFit.of(72, 88, "Christmas lights outside", "");
        assertTrue(longName.smallText);
        assertEquals(3, longName.lines);
    }

    @Test
    public void theStateLineGoesBeforeTheButtonShrinks() {
        ActionButtonFit roomy = ActionButtonFit.of(124, 150, "Garage", "Closed");
        assertTrue(roomy.state);
        ActionButtonFit tight = ActionButtonFit.of(64, 76, "Garage", "Closed");
        assertTrue(tight.label);
        assertFalse(tight.state);
        assertTrue(tight.button >= 0.8f * (64 - 2 * ActionButtonFit.PAD));
    }

    @Test
    public void aTinyWidgetIsTheButtonAlone() {
        ActionButtonFit f = ActionButtonFit.of(40, 40, "Garage", "Closed");
        assertFalse(f.label);
        assertFalse(f.state);
        assertEquals(34f, f.button, 0.01f);
    }

    @Test
    public void theButtonStopsGrowingOnAHugeWidget() {
        assertEquals(ActionButtonFit.MAX_BUTTON, ActionButtonFit.of(400, 400, "Garage", "").button, 0.01f);
    }

    @Test
    public void linesCountWholeWords() {
        assertEquals(1, ActionButtonFit.linesFor("Garage", 66, 6.6f));
        assertEquals(2, ActionButtonFit.linesFor("Office lamp", 66, 6.6f));
        assertEquals(0, ActionButtonFit.linesFor("  ", 66, 6.6f));
        // A word longer than a line breaks inside it.
        assertEquals(2, ActionButtonFit.linesFor("Supercalifragilistic", 66, 6.6f));
    }
}
