package com.jarvisos.app;

import android.app.Activity;
import android.os.Bundle;

/**
 * What long-press-home (or the assistant gesture) opens once this app holds the
 * system assistant role: no screen of its own, it starts a spoken turn and gets
 * out of the way.
 */
public final class AssistActivity extends Activity {

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        AssistService.turn(this, false);
        finish();
    }
}
