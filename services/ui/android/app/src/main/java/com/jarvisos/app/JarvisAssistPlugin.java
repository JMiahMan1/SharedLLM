package com.jarvisos.app;

import android.Manifest;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * The WebView's way into {@link AssistService}: start a turn, listen for the
 * wake word, stop, and hear every state and word as it happens.
 *
 * The microphone permission is asked here rather than left to the WebView: a
 * foreground service that cannot record is a notification with nothing behind
 * it, and a refused permission must say so instead of showing "Listening".
 */
@CapacitorPlugin(
        name = "JarvisAssist",
        permissions = {@Permission(alias = "microphone", strings = {Manifest.permission.RECORD_AUDIO})}
)
public class JarvisAssistPlugin extends Plugin {

    private final AssistService.Listener listener = (type, text, level) -> {
        JSObject event = new JSObject();
        event.put("type", type);
        event.put("text", text);
        event.put("level", level);
        notifyListeners("assist", event);
    };

    @Override
    public void load() {
        AssistService.addListener(listener);
    }

    @Override
    protected void handleOnDestroy() {
        AssistService.removeListener(listener);
        super.handleOnDestroy();
    }

    @PluginMethod
    public void start(PluginCall call) {
        if (getPermissionState("microphone") != com.getcapacitor.PermissionState.GRANTED) {
            requestPermissionForAlias("microphone", call, "microphoneThenStart");
            return;
        }
        begin(call);
    }

    @PermissionCallback
    private void microphoneThenStart(PluginCall call) {
        if (getPermissionState("microphone") != com.getcapacitor.PermissionState.GRANTED) {
            call.reject("Jarvis needs the microphone to hear you.");
            return;
        }
        begin(call);
    }

    private void begin(PluginCall call) {
        if (!new VoiceClient(getContext()).signedIn()) {
            call.reject("Sign in to Jarvis on this phone first.");
            return;
        }
        AssistService.turn(getContext(), call.getBoolean("wake", false));
        call.resolve();
    }

    @PluginMethod
    public void stop(PluginCall call) {
        AssistService.stop(getContext());
        call.resolve();
    }

    @PluginMethod
    public void stopSpeaking(PluginCall call) {
        AssistService.stopSpeaking(getContext());
        call.resolve();
    }

    /** What the UI shows on open, so a running turn is never mistaken for silence. */
    @PluginMethod
    public void status(PluginCall call) {
        JSObject out = new JSObject();
        out.put("running", AssistService.running());
        out.put("state", AssistService.state());
        call.resolve(out);
    }

    /** The phrase the phone wakes to ("hey jarvis" unless the household set another). */
    @PluginMethod
    public void setWakePhrase(PluginCall call) {
        String phrase = call.getString("phrase", "hey jarvis");
        if (phrase == null || phrase.trim().isEmpty()) {
            call.reject("A wake phrase cannot be empty.");
            return;
        }
        getContext().getSharedPreferences(VoiceClient.PREFS, android.content.Context.MODE_PRIVATE)
                .edit().putString("jarvis_wake_phrase", phrase.trim().toLowerCase()).apply();
        call.resolve();
    }

    /**
     * Whether this phone can hand an app the assistant role, and whether it
     * already has. The sheet offers it only when the answer is yes and no.
     */
    @PluginMethod
    public void assistantRole(PluginCall call) {
        JSObject out = new JSObject();
        boolean can = AssistEntryRules.canHoldAssistantRole(android.os.Build.VERSION.SDK_INT);
        out.put("canHold", can);
        out.put("held", can && roleHeld());
        call.resolve(out);
    }

    /** The one tap that makes Jarvis the phone's assistant (long-press home). */
    @PluginMethod
    public void requestAssistantRole(PluginCall call) {
        if (!AssistEntryRules.canHoldAssistantRole(android.os.Build.VERSION.SDK_INT)) {
            call.reject("This phone is too old to hand an app the assistant role.");
            return;
        }
        try {
            openRoleRequest(call);
        } catch (Exception e) {
            // Not "no": the phone may still let the person choose Jarvis by hand.
            openAssistantSettings(call);
        }
    }

    /**
     * Called by Capacitor when the role dialog closes, whichever way it went.
     *
     * The annotation and the ActivityResult parameter are both required: Capacitor 8
     * looks the method up by name and only calls it when it is annotated, so the
     * variant that took just a PluginCall was never invoked and "make Jarvis your
     * assistant" stayed pending for ever.
     */
    @com.getcapacitor.annotation.ActivityCallback
    public void roleResult(PluginCall call, androidx.activity.result.ActivityResult result) {
        if (call != null) call.resolve();
    }

    /**
     * Android's own picker (Settings -> Default digital assistant), which is where a
     * Samsung side button, a long-press of Home and every other assistant key are
     * assigned. Used when this phone will not hand out the assistant role directly.
     */
    private void openAssistantSettings(PluginCall call) {
        try {
            getContext().startActivity(new android.content.Intent(
                            android.provider.Settings.ACTION_VOICE_INPUT_SETTINGS)
                    .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));
        } catch (Exception ignored) {
            // Some builds hide that screen; nothing more can be opened for them.
        }
        if (call != null) call.resolve();
    }

    @android.annotation.TargetApi(29)
    private boolean roleHeld() {
        android.app.role.RoleManager roles =
                (android.app.role.RoleManager) getContext().getSystemService(android.app.role.RoleManager.class);
        return roles != null && roles.isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT);
    }

    @android.annotation.TargetApi(29)
    private void openRoleRequest(PluginCall call) {
        android.app.role.RoleManager roles =
                (android.app.role.RoleManager) getContext().getSystemService(android.app.role.RoleManager.class);
        if (roles == null || !roles.isRoleAvailable(android.app.role.RoleManager.ROLE_ASSISTANT)) {
            // No role dialog on this build (One UI in particular): send the person to
            // the Digital Assistant picker instead of answering with a dead end.
            openAssistantSettings(call);
            return;
        }
        if (roles.isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT)) {
            call.resolve();
            return;
        }
        startActivityForResult(call, roles.createRequestRoleIntent(android.app.role.RoleManager.ROLE_ASSISTANT),
                "roleResult");
    }


    /**
     * Only the microphone permission. The browser turn does the recording, so a
     * tap needs Android's grant without starting a microphone foreground service
     * (which Android 12+ can refuse from a tap, taking the app down with it).
     */
    @PluginMethod
    public void requestMicrophone(PluginCall call) {
        JSObject out = new JSObject();
        if (getPermissionState("microphone") == com.getcapacitor.PermissionState.GRANTED) {
            out.put("granted", true);
            call.resolve(out);
            return;
        }
        requestPermissionForAlias("microphone", call, "microphoneResult");
    }

    @PermissionCallback
    private void microphoneResult(PluginCall call) {
        JSObject out = new JSObject();
        out.put("granted", getPermissionState("microphone") == com.getcapacitor.PermissionState.GRANTED);
        if (call != null) call.resolve(out);
    }

}
