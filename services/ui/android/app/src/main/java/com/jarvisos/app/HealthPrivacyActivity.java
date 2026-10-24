package com.jarvisos.app;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.util.TypedValue;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * What Jarvis does with Health Connect data, in plain words. Health Connect
 * links here from its permission sheet ("Read privacy policy") and from its
 * app-permissions page; it refuses to show the sheet to an app without one.
 */
public class HealthPrivacyActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        TextView text = new TextView(this);
        int pad = (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 20, getResources().getDisplayMetrics());
        text.setPadding(pad, pad, pad, pad);
        text.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        text.setTextColor(Color.parseColor("#E2E8F0"));
        text.setText(getString(R.string.health_connect_privacy));
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.parseColor("#0F172A"));
        scroll.addView(text);
        setContentView(scroll);
        setTitle(R.string.health_connect_privacy_title);
    }
}
