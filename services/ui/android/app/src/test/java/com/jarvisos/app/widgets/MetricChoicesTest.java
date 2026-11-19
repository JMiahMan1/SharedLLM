package com.jarvisos.app.widgets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.List;
import org.json.JSONObject;
import org.junit.Test;

/** The metric widget offers the home's own number sensors; every id and name here is made up. */
public class MetricChoicesTest {

    private static JSONObject entity(String name, String state, String unit) throws Exception {
        JSONObject e = new JSONObject().put("friendly_name", name).put("state", state);
        if (unit != null) e.put("unit_of_measurement", unit);
        return e;
    }

    @Test
    public void onlySensorsThatReadANumberAreOffered() throws Exception {
        JSONObject byId = new JSONObject()
            .put("sensor.pantry_temperature", entity("Pantry Temperature", "71.46", "°F"))
            .put("sensor.attic_humidity", entity("Attic Humidity", "48", "%"))
            .put("sensor.front_door_status", entity("Front Door Status", "closed", null))
            .put("sensor.broken", entity("Broken", "unavailable", null))
            .put("light.pantry", entity("Pantry", "on", null));
        List<MetricChoices.Choice> out = MetricChoices.from(byId);
        assertEquals(2, out.size());
        assertEquals("sensor.attic_humidity", out.get(0).entityId);
        assertEquals("48 %", out.get(0).reading);
        assertEquals("Pantry Temperature", out.get(1).name);
        assertEquals("71.5 °F", out.get(1).reading);
    }

    @Test
    public void aSensorWithNoNameShowsItsId() throws Exception {
        JSONObject byId = new JSONObject().put("sensor.x1", new JSONObject().put("state", "3"));
        assertEquals("sensor.x1", MetricChoices.from(byId).get(0).name);
    }

    @Test
    public void nothingIsOfferedFromNothing() {
        assertTrue(MetricChoices.from(null).isEmpty());
        assertFalse(MetricChoices.isMetric("sensor.x", null));
        assertFalse(MetricChoices.isMetric(null, new JSONObject()));
    }
}
