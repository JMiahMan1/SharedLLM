package com.jarvisos.app;

import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.telecom.CallAudioState;
import android.telecom.CallEndpoint;
import android.telecom.Connection;
import android.telecom.ConnectionRequest;
import android.telecom.ConnectionService;
import android.telecom.DisconnectCause;
import android.telecom.PhoneAccountHandle;
import android.telecom.TelecomManager;
import android.util.Log;
import java.util.List;

/**
 * Telecom's side of a family drop-in ({@link DropInCall}): a self-managed
 * call that answers itself, plays on the speaker (or a headset when one is
 * connected), and ends when the person or the app hangs up.
 */
public class DropInConnectionService extends ConnectionService {
    private static final String TAG = "DropInConnection";

    @Override
    public Connection onCreateIncomingConnection(PhoneAccountHandle account, ConnectionRequest request) {
        Bundle extras = request.getExtras();
        Bundle inner = extras == null ? null : extras.getBundle(TelecomManager.EXTRA_INCOMING_CALL_EXTRAS);
        Bundle source = inner != null ? inner : extras;
        String room = source == null ? null : source.getString(DropInCall.EXTRA_ROOM);
        String name = source == null ? null : source.getString(DropInCall.EXTRA_NAME);
        DropInConnection connection = new DropInConnection(room, name == null ? "Family call" : name);
        DropInCall.current = connection;
        return connection;
    }

    @Override
    public void onCreateIncomingConnectionFailed(PhoneAccountHandle account, ConnectionRequest request) {
        // Telecom said no (another call started meanwhile). The ring timed out
        // on the server side as usual; nothing else to undo.
        Log.w(TAG, "Telecom refused the drop-in");
        DropInCall.current = null;
    }

    final class DropInConnection extends Connection {
        private final String room;
        private boolean finished = false;

        String room() {
            return room;
        }

        DropInConnection(String room, String name) {
            this.room = room;
            setConnectionProperties(PROPERTY_SELF_MANAGED);
            setAudioModeIsVoip(true);
            setAddress(Uri.fromParts("jarvis", room == null ? "family" : room, null),
                    TelecomManager.PRESENTATION_ALLOWED);
            setCallerDisplayName(name, TelecomManager.PRESENTATION_ALLOWED);
            setInitializing();
        }

        /** Telecom asks us to show the incoming call: a drop-in answers instead. */
        @Override
        public void onShowIncomingCallUi() {
            answerNow();
        }

        @Override
        public void onAnswer() {
            answerNow();
        }

        private void answerNow() {
            if (finished || getState() == STATE_ACTIVE) return;
            if (room == null) {
                finish();
                return;
            }
            setActive();
            DropInCall.ding();
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                useSpeakerUnlessHeadset(getCallAudioState());
            }
            CharSequence name = getCallerDisplayName();
            DropInCall.join(DropInConnectionService.this, room, name == null ? "Family call" : name.toString());
        }

        /** A drop-in is hands-free: speaker, unless a headset or car is connected. */
        @SuppressWarnings("deprecation")
        private void useSpeakerUnlessHeadset(CallAudioState state) {
            if (state == null) return;
            int routes = state.getSupportedRouteMask();
            if ((routes & (CallAudioState.ROUTE_BLUETOOTH | CallAudioState.ROUTE_WIRED_HEADSET)) != 0) return;
            if ((routes & CallAudioState.ROUTE_SPEAKER) != 0) setAudioRoute(CallAudioState.ROUTE_SPEAKER);
        }

        @Override
        public void onAvailableCallEndpointsChanged(List<CallEndpoint> endpoints) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE || endpoints == null) return;
            CallEndpoint speaker = null;
            for (CallEndpoint e : endpoints) {
                int type = e.getEndpointType();
                if (type == CallEndpoint.TYPE_BLUETOOTH || type == CallEndpoint.TYPE_WIRED_HEADSET) return;
                if (type == CallEndpoint.TYPE_SPEAKER) speaker = e;
            }
            if (speaker != null) {
                requestCallEndpointChange(speaker, getMainExecutor(), result -> { });
            }
        }

        @Override
        public void onDisconnect() {
            DropInCall.hangUp(DropInConnectionService.this);
        }

        @Override
        public void onAbort() {
            DropInCall.hangUp(DropInConnectionService.this);
        }

        @Override
        public void onReject() {
            DropInCall.hangUp(DropInConnectionService.this);
        }

        /** End the Telecom call; safe to call more than once. */
        void finish() {
            if (finished) return;
            finished = true;
            if (DropInCall.current == this) DropInCall.current = null;
            setDisconnected(new DisconnectCause(DisconnectCause.LOCAL));
            destroy();
        }
    }
}
