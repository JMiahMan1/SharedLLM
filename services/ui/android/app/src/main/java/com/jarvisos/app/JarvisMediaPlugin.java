package com.jarvisos.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * The web player's link to {@link MediaPlaybackService}: it reports what is
 * playing, and hears the notification / lock-screen / headset buttons back as
 * {@code action} events.
 */
@CapacitorPlugin(name = "JarvisMedia")
public class JarvisMediaPlugin extends Plugin {

    @Override
    public void load() {
        MediaPlaybackService.listener = action -> {
            JSObject data = new JSObject();
            data.put("action", action);
            notifyListeners("action", data);
        };
    }

    @PluginMethod
    public void update(PluginCall call) {
        Integer volume = call.getInt("volume");
        MediaPlaybackService.update(
                getContext(),
                call.getString("title", "Jarvis"),
                call.getString("artist", ""),
                Boolean.TRUE.equals(call.getBoolean("playing", false)),
                call.getString("playerId"),
                call.getString("playerName"),
                volume != null ? volume : 70);
        call.resolve();
    }

    /**
     * Take playback back from the native player that kept it going while the
     * app was closed; {@code playing} says whether to carry on in the web player.
     */
    @PluginMethod
    public void reclaim(PluginCall call) {
        JSObject result = new JSObject();
        result.put("playing", MediaPlaybackService.reclaim());
        call.resolve(result);
    }

    @PluginMethod
    public void stop(PluginCall call) {
        MediaPlaybackService.stop(getContext());
        call.resolve();
    }

    @Override
    protected void handleOnDestroy() {
        MediaPlaybackService.listener = null;
        // Playing: keep the music going natively. Otherwise this stops the service.
        MediaPlaybackService.handOff(getContext());
    }
}
