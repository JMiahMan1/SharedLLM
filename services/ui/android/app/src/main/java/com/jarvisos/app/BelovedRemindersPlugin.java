package com.jarvisos.app;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Beloved's special-day reminders, set on this phone (src/plugins/belovedReminders.ts).
 *
 * The server cannot read a couple's dates, so it cannot remind them: the page
 * works out the coming reminders after it decrypts them and hands them here.
 * {@code sync} replaces every reminder set before; {@code clear} cancels them.
 * The text is whatever the page sent, which is generic unless the phone's
 * owner turned on "Show what the day is in reminders". See
 * {@link BelovedReminderReceiver} for the alarms and the notification.
 */
@CapacitorPlugin(name = "JarvisBelovedReminders")
public class BelovedRemindersPlugin extends Plugin {

    @PluginMethod
    public void sync(PluginCall call) {
        JSArray given = call.getArray("reminders");
        JSONArray clean = new JSONArray();
        try {
            for (int i = 0; given != null && i < given.length() && clean.length() < BelovedReminderReceiver.MAX; i++) {
                JSONObject r = given.getJSONObject(i);
                JSONObject one = new JSONObject();
                one.put("id", r.getInt("id"));
                one.put("at", r.getLong("at"));
                one.put("title", r.optString("title", "Jarvis"));
                one.put("body", r.optString("body", ""));
                clean.put(one);
            }
        } catch (JSONException e) {
            call.reject("Those reminders could not be read.");
            return;
        }
        int armed = BelovedReminderReceiver.replace(getContext(), clean);
        JSObject result = new JSObject();
        result.put("scheduled", armed);
        call.resolve(result);
    }

    @PluginMethod
    public void clear(PluginCall call) {
        BelovedReminderReceiver.replace(getContext(), new JSONArray());
        JSObject result = new JSObject();
        result.put("scheduled", 0);
        call.resolve(result);
    }
}
