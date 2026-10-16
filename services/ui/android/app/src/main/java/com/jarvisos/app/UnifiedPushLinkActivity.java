package com.jarvisos.app;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

/**
 * The UnifiedPush link activity ({@code unifiedpush://link}): an app that
 * wants to use Jarvis as its distributor opens it for a result and gets back
 * an immutable PendingIntent whose creator tells it Jarvis's package name.
 * Shows nothing and finishes at once. Disabled while nobody is signed in.
 */
public class UnifiedPushLinkActivity extends Activity {
    /** The spec's placeholder target; the PendingIntent is only read, never sent. */
    private static final String DUMMY_APP = "org.unifiedpush.dummy_app";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Uri data = getIntent() != null ? getIntent().getData() : null;
        boolean link = data != null && "unifiedpush".equals(data.getScheme()) && "link".equals(data.getHost());
        if (link && getCallingPackage() != null) {
            PendingIntent pi = PendingIntent.getBroadcast(
                    this, 0, new Intent().setPackage(DUMMY_APP), PendingIntent.FLAG_IMMUTABLE);
            setResult(RESULT_OK, new Intent().putExtra(UnifiedPushDistributor.EXTRA_PI, pi));
        } else {
            setResult(RESULT_CANCELED);
        }
        finish();
    }
}
