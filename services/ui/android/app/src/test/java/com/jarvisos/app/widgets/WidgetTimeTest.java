package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;

import java.util.TimeZone;
import org.junit.Test;

/** Desktop-JVM tests for how widgets read and show times. */
public class WidgetTimeTest {
    private static final TimeZone DENVER = TimeZone.getTimeZone("America/Denver");

    @Test
    public void readsInstantsLocalTimesAndDates() {
        long z = WidgetTime.parse("2026-11-09T04:15:00Z", DENVER);
        long off = WidgetTime.parse("2026-11-08T21:15:00-07:00", DENVER);
        long local = WidgetTime.parse("2026-11-08T21:15:00", DENVER);
        long micros = WidgetTime.parse("2026-11-08T21:15:00.123456-07:00", DENVER);
        assertEquals(z, off);
        assertEquals(z, local);
        assertEquals(z, micros);
        assertEquals("9:15 PM", WidgetTime.clock(z, DENVER));
        assertEquals(0, WidgetTime.parse("not a time", DENVER));
        assertEquals(0, WidgetTime.parse(null, DENVER));
    }

    @Test
    public void namesTheDay() {
        long now = WidgetTime.parse("2026-11-08T09:00:00", DENVER);
        assertEquals("Today", WidgetTime.day(WidgetTime.parse("2026-11-08T22:00:00", DENVER), now, DENVER));
        assertEquals("Tomorrow", WidgetTime.day(WidgetTime.parse("2026-11-09", DENVER), now, DENVER));
        assertEquals("Wed", WidgetTime.day(WidgetTime.parse("2026-11-11T10:00:00", DENVER), now, DENVER));
        assertEquals("Nov 20", WidgetTime.day(WidgetTime.parse("2026-11-20T10:00:00", DENVER), now, DENVER));
    }

    @Test
    public void minutesReadAsHoursAndMinutes() {
        assertEquals("1h 20m", WidgetTime.minutes(80));
        assertEquals("2h", WidgetTime.minutes(120));
        assertEquals("45m", WidgetTime.minutes(45));
        assertEquals("0m", WidgetTime.minutes(-5));
    }
}
