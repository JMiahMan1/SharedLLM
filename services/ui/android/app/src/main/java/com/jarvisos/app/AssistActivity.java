package com.jarvisos.app;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;

/**
 * What every assistant trigger opens: long-press home, the Quick Settings tile,
 * the home-screen widget, a Bluetooth voice button, or Jarvis as the phone's
 * Digital Assistant.
 *
 * It used to start a spoken turn in an invisible service and get out of the way,
 * which on a phone with no microphone permission (or no saved session) meant the
 * trigger did nothing at all - no screen, no sound, no explanation. Now it brings
 * up the real assistant screen with the sheet already open, so the person can
 * see it listening, read the words as they arrive, and hear the answer.
 */
public final class AssistActivity extends Activity {

    /** The address that opens the dashboard with the Ask Jarvis sheet up. */
    static final String ASSIST_PATH = "/?voice=1";

    /** The intent that opens the assistant, from anywhere (a tile, a service, a widget). */
    static Intent askJarvis(Context context) {
        Intent intent = new Intent(context, MainActivity.class);
        intent.putExtra(PushNotifier.EXTRA_OPEN_PATH, ASSIST_PATH);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return intent;
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        try {
            startActivity(askJarvis(this));
        } catch (RuntimeException ignored) {
            // Android refused to bring the app forward; the trigger simply ends.
        }
        finish();
    }
}
